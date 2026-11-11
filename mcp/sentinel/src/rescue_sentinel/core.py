"""监听器核心（docs/design-v3.md 7.1）：不经模型，按矿井时钟检测、去重、写事件库。

一次 tick()：
1. 到了检测间隔（矿井时间）就按技能的采集清单（check）取证据、运行 fire-assessment/check（与一次性检测、研判同一套判据），
   基线 = 上一次已上报的状态；
   - 等级/阶段升高、新实时报警：立即告警；数据类异常（测点失效、数据中断）连续 confirm_checks 次才告警；
   - 告警后基线更新为当前状态：同一异常持续存在不重复告警；
   - 不比基线更糟，连续 recover_checks 次后基线下调；回到正常则写“恢复”，事件转“已稳定”；
   - 取数/检测连续失败 degrade_failures 次：写“监听降级”告警（恢复后写恢复）。
2. 接入矿井自带报警（get_alarms）：新的、仍越限且未确认的报警并入对应工作面的事件（同一轮已为该工作面告警的只记已见）。
3. 人工确认超时：未决的确认（approval_pending 没有 approval_decided）等待超过动作分级表的批准超时（approval_timeout_min，真实时间），
   写 approval_escalated（提醒谁：escalate_to）和告警，只升级一次；
4. 跟踪计划到点（followup_added 的 due_at ≤ 矿井时间）写 followup_due 和告警，唤醒智能体复评。

状态（每个工作面的基线、连续次数、已见报警）落盘 state.json，重启后接着监听，不重复告警。
告警写进事件库；唤醒值班会话是 DSH 事件插件的事（本进程不依赖 DSH）。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from rescue_contract.collect import collect, parse_collect_spec
from rescue_contract.incidents import IncidentStore
from rescue_contract.skills import default_root, load_skill, run_script

CallTool = Callable[[str, dict[str, Any]], Awaitable[Any]]
BY = "sentinel"


def _unwrap(v: Any) -> Any:
    return v["result"] if isinstance(v, dict) and len(v) == 1 and isinstance(v.get("result"), list) else v


def _mine_dt(t: str) -> datetime:
    return datetime.fromisoformat(t[:19])


def _is_nominal(state: dict[str, Any]) -> bool:
    return (
        state["level"] == "none" and state["stage"] == "no_sign" and not state["alarm_keys"]
        and not state["failed"] and not state["missing_groups"] and not state.get("stale")
    )  # fmt: skip


class Sentinel:
    def __init__(self, call: CallTool, store: IncidentStore, *, skills_root: Path | None = None, state_dir: Path | None = None, python: str | None = None) -> None:
        self.call, self.store, self.python = call, store, python
        root = skills_root or default_root()
        self.skill = load_skill(root / "fire-assessment")
        self.spec = parse_collect_spec((self.skill.dir / "references" / "collect.yaml").read_text(encoding="utf-8"))
        watch = yaml.safe_load((self.skill.dir / "references" / "watch.yaml").read_text(encoding="utf-8"))
        # 监视哪些工作面：缺省取技能参数（watch.yaml 的 panels）；RESCUE_WATCH_PANELS=211016,1202 覆盖（接入别的矿井或换了演示矿井名称时用）
        env_panels = [p.strip() for p in os.environ.get("RESCUE_WATCH_PANELS", "").split(",") if p.strip()]
        self.panels: list[str] = env_panels or list(watch["panels"])
        self.cfg: dict[str, Any] = watch["sentinel"]
        self.state_dir = state_dir or store.dir / "sentinel"
        self.state_path = self.state_dir / "state.json"
        self.state: dict[str, Any] = self._load()

    @staticmethod
    def wall() -> float:
        return time.time()

    # ── 状态 ──
    def _load(self) -> dict[str, Any]:
        try:
            s = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            s = {}
        s.setdefault("last_check", None)
        s.setdefault("panels", {})
        s.setdefault("seen_alarms", [])
        return s

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(f".state.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def heartbeat(self, now: str | None, error: str | None = None) -> None:
        """心跳（真实时间）：控制台据此显示“监听中断”。"""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        p = self.state_dir / "heartbeat.json"
        tmp = p.with_name(f".heartbeat.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"wall": time.time(), "mine_now": now, "error": error, "panels": self.panels}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def _panel(self, panel: str) -> dict[str, Any]:
        return self.state["panels"].setdefault(panel, {"baseline": None, "anomaly_streak": 0, "settle_streak": 0, "failures": 0, "degraded": False})

    # ── 一次检测 ──
    def _check(self, ev: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
        data: dict[str, Any] = {"evidence": ev}
        if baseline is not None:
            data["baseline"] = baseline
        r = run_script(self.skill.scripts["check"], data, python=self.python)
        if not r.ok:
            raise RuntimeError(f"fire-assessment/check：{r.status} {r.detail}")
        return r.output["result"]

    def _incident_for(self, category: str, panel: str | None, at: str, summary: str) -> str:
        found = self.store.find_open(category, panel)
        return found["incident_id"] if found else self.store.open(at, category=category, panel=panel, summary=summary, by=BY)

    def _alert(self, incident_id: str, at: str, cause: str, summary: str, **data: Any) -> dict[str, Any]:
        # 计划内的跟踪到点不是新的异常：已稳定的事件保持已稳定（2026-11-10 演示复看：每次到点都在 监测中/已稳定 之间来回跳）
        if cause != "followup_due" and self.store.get(incident_id)["status"] == "stable":
            self.store.append(incident_id, at, "status", BY, {"status": "monitoring", "reason": "稳定后再次出现告警"})
        return self.store.append(incident_id, at, "alert", BY, {"cause": cause, "summary": summary, **data})

    async def _check_panel(self, panel: str, now: str) -> list[dict[str, Any]]:
        ps, out = self._panel(panel), []
        try:
            ev = await collect(self.call, self.spec, "check", panel)
            res = self._check(ev, ps["baseline"])
        except Exception as err:  # noqa: BLE001  取数、脚本任何失败都算一次失败
            ps["failures"] += 1
            if ps["failures"] >= self.cfg["degrade_failures"] and not ps["degraded"]:
                ps["degraded"] = True
                iid = self._incident_for("monitor", panel, now, f"{panel} 工作面监听降级")
                summary = f"{panel} 工作面监听降级：连续 {ps['failures']} 次检测失败（{str(err)[:200]}）。此期间的“无异常”不可信"
                self.store.append(iid, now, "degraded", BY, {"summary": summary, "failures": ps["failures"]})
                out.append(self._alert(iid, now, "degraded", summary))
            return out
        ps["failures"] = 0
        if ps["degraded"]:
            ps["degraded"] = False
            found = self.store.find_open("monitor", panel)
            if found:
                self.store.append(found["incident_id"], now, "recovered", BY, {"of": "degraded", "summary": f"{panel} 工作面监听恢复"})
                self.store.append(found["incident_id"], now, "status", BY, {"status": "closed", "reason": "监听恢复"})
        state = res["state"]
        base = ps["baseline"]["result"]["state"] if ps["baseline"] else None
        as_of = res["as_of"]
        brief = {"as_of": as_of, "level": res["level"], "level_name": res["level_name"], "stage": res["stage"], "stage_name": res["stage_name"], "alarm_count": res["alarm_count"], "reasons": res["reasons"]}
        new_base = {"result": {"panel": res["panel"], "as_of": as_of, "state": state}}
        if res["anomaly"]:
            ps["settle_streak"] = 0
            ps["anomaly_streak"] += 1
            if res["escalated"] or ps["anomaly_streak"] >= self.cfg["confirm_checks"]:
                summary = f"{panel} 工作面：" + "；".join(res["reasons"])
                iid = self._incident_for("fire", panel, now, f"{panel} 工作面异常")
                out.append(self._alert(iid, now, "anomaly", summary, reasons=res["reasons"], check=brief))
                ps["baseline"], ps["anomaly_streak"] = new_base, 0
            return out
        ps["anomaly_streak"] = 0
        if base is not None and state != base:
            ps["settle_streak"] += 1
            if ps["settle_streak"] >= self.cfg["recover_checks"]:
                ps["settle_streak"] = 0
                ps["baseline"] = None if _is_nominal(state) else new_base
                if _is_nominal(state):
                    found = self.store.find_open("fire", panel)
                    if found:
                        msg = f"{panel} 工作面监测指标已恢复正常（连续 {self.cfg['recover_checks']} 次检测不再异常）"
                        self.store.append(found["incident_id"], now, "recovered", BY, {"summary": msg, "check": brief})
                        if found["status"] != "closed":
                            self.store.append(found["incident_id"], now, "status", BY, {"status": "stable", "reason": "监测指标恢复正常"})
        else:
            ps["settle_streak"] = 0
        return out

    async def _mine_alarms(self, now: str, alerted_panels: set[str]) -> list[dict[str, Any]]:
        try:
            alarms = _unwrap(await self.call("get_alarms", {"hours": 24, "active_only": True}))
            sensors = {x["id"]: x for x in _unwrap(await self.call("list_sensors", {}))} if alarms else {}
        except Exception:  # noqa: BLE001  矿井不提供报警平台时不影响主检测
            return []
        seen, out = set(self.state["seen_alarms"]), []
        for a in alarms:
            if a["alarm_id"] in seen:
                continue
            seen.add(a["alarm_id"])
            if a.get("acknowledged"):
                continue
            panel = (sensors.get(a.get("sensor_id")) or {}).get("panel")
            if panel in alerted_panels:
                continue  # 同一轮已为该工作面告警：风险检测的实时报警已覆盖
            iid = self._incident_for("fire", panel, now, f"{panel or '全矿'} 矿井报警")
            out.append(self._alert(iid, now, "mine_alarm", f"矿井报警平台：{a['message']}（{a['alarm_id']}）", alarm_id=a["alarm_id"], sensor_id=a.get("sensor_id"), value=a.get("value"), threshold=a.get("threshold")))
        self.state["seen_alarms"] = sorted(seen)[-500:]
        return out

    def _followups(self, now: str) -> list[dict[str, Any]]:
        out = []
        for v in self.store.list():
            for f in v["followups"]:
                if f["state"] == "pending" and _mine_dt(f["due_at"]) <= _mine_dt(now):
                    self.store.append(v["incident_id"], now, "followup_due", BY, {"followup_id": f["followup_id"]})
                    out.append(self._alert(v["incident_id"], now, "followup_due", f"跟踪计划到点：{f.get('expect') or f['followup_id']}（计划 {f['due_at']}）", followup_id=f["followup_id"], expect=f.get("expect")))
        return out

    def _approvals(self, now: str, wall_now: float) -> list[dict[str, Any]]:
        """批准超时升级（design-v3 6.3）：未决的人工确认等待超过批准超时，写 approval_escalated 和告警，只升级一次。"""
        out = []
        for v in self.store.list():
            for a in v["pending_approvals"]:
                limit = a.get("timeout_min")
                if a["escalated"] or not limit:
                    continue
                waited_s = wall_now - datetime.fromisoformat(a["asked_wall"]).timestamp()
                if waited_s < limit * 60:
                    continue
                # on_timeout 为 approve / reject 的由审批插件到点自动处理（结果记 approval_decided，确认就不再未决）；
                # 这里只在超过两倍批准超时仍未决时兜底升级（插件或 DSH 已经挂了，没有人在处理）
                if a.get("on_timeout") in ("approve", "reject") and waited_s < 2 * limit * 60:
                    continue
                waited_min = int(waited_s // 60)
                to = a.get("escalate_to") or "上一级"
                self.store.append(v["incident_id"], now, "note", BY, {"type": "approval_escalated", "approval_key": a["approval_key"], "tool": a["tool"], "escalate_to": to, "waited_min": waited_min})
                out.append(self._alert(v["incident_id"], now, "approval_timeout", f"{a['tool']} 等待人工确认已超过 {limit} 分钟（已等 {waited_min} 分钟，确认人：{a.get('confirmer') or '未指定'}），已提醒{to}", approval_key=a["approval_key"], tool=a["tool"], escalate_to=to))
        return out

    async def tick(self) -> list[dict[str, Any]]:
        """返回本次产生的告警条目（可能为空）。矿井时钟读不到时抛错，由调用方记心跳。
        批准超时按真实时间算、与矿井无关，而矿井接口故障时人工确认最需要升级：取不到矿井时钟也先查它（条目时间用最后一次已知的
        矿井时间，没有就用本机时间），写进事件库后再把取时钟的错抛出去。"""
        try:
            now = str((await self.call("get_clock", {}))["now"])[:19]
        except Exception:
            fallback = self.state["last_check"] or datetime.fromtimestamp(self.wall()).isoformat(timespec="seconds")
            self._approvals(fallback, self.wall())  # 告警条目已进事件库，唤醒插件会投递；这里不返回，错误照常上抛
            self._save()
            raise
        out: list[dict[str, Any]] = []
        last = self.state["last_check"]
        if last is not None and _mine_dt(now) < _mine_dt(last):
            # 矿井时钟倒退 = 模拟矿井被重置（演示/评测）：旧基线和已见报警属于上一段历史，丢掉，否则新历史里的异常会被当成“不比基线更糟”
            self.state["panels"], self.state["seen_alarms"], last = {}, [], None
        if last is None or (_mine_dt(now) - _mine_dt(last)).total_seconds() >= self.cfg["interval_min"] * 60:
            self.state["last_check"] = now
            alerted: set[str] = set()
            for panel in self.panels:
                got = await self._check_panel(panel, now)
                if got:
                    alerted.add(panel)
                out += got
            out += await self._mine_alarms(now, alerted)
        out += self._followups(now)
        out += self._approvals(now, self.wall())
        self._save()
        self.heartbeat(now)
        return out
