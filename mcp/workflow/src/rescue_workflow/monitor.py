"""W0 值守进程：不经过大模型，定时检查实时报警、预警等级和自燃阶段，有变化时生成研判快照并写值守事件。

    python -m rescue_workflow.monitor          # scripts/mcp-up.sh 随其他服务一起启动

事件写入 var/monitor/events.jsonl（RESCUE_MONITOR_DIR）：指挥 Agent 用 workflow 的 get_monitor_events 读取，
控制台同时打印。推送到调度台、移动端的通道尚未实现（见 fire_model/alarms.py 的 TODO）。
DSH 0.2.1-rc.4 的 headless 入口不支持 Agent 预设，所以不自动拉起智能体会话。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from rescue_contract.client import MineData
from rescue_contract.models import FireAreaStatus, Snapshot, SnapshotKind
from rescue_contract.serving import DEFAULT_PORTS
from rescue_contract.snapshots import SnapshotStore, new_id
from rescue_fire_model import alarms, effect, explosion, warning

from . import assess, config, reports

log = logging.getLogger("rescue.monitor")
MONITOR_DIR_ENV = "RESCUE_MONITOR_DIR"


def monitor_dir() -> Path:
    return Path(os.environ.get(MONITOR_DIR_ENV) or Path(os.environ.get("RESCUE_HOME", ".")) / "var" / "monitor")


def read_events(since: datetime | None = None, panel: str | None = None, directory: Path | None = None) -> list[dict[str, Any]]:
    path = (directory or monitor_dir()) / "events.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if panel and e.get("panel") != panel:
            continue
        if since and datetime.fromisoformat(e["mine_time"]) < since:
            continue
        out.append(e)
    return out


class Monitor:
    """一次 tick 检查一遍；状态（上次等级、阶段、报警、按班复评时刻）保存在内存和 state.json。"""

    def __init__(self, md: MineData, store: SnapshotStore, directory: Path | None = None, public_url: str | None = None) -> None:
        self.md, self.store = md, store
        self.dir = directory or monitor_dir()
        self.cfg = config.load()["monitor"]
        self.xc = explosion.load_constants()
        self.public_url = public_url or f"http://127.0.0.1:{os.environ.get('RESCUE_FIRE_MODEL_PORT', DEFAULT_PORTS['fire_model'])}"
        self.state: dict[str, Any] = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        path = self.dir / "state.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"last_check": None, "panels": {}}

    def _save_state(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "state.json").write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _emit(self, event: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        print(f"[值守] {event['mine_time']} {event['panel']} {event['kind']}：{event['message']}", flush=True)

    def _save(self, kind: SnapshotKind, panel: str, as_of: str, inputs: dict, result: dict, report) -> Snapshot:
        at = datetime.fromisoformat(as_of)
        sid = new_id(at)
        snap = Snapshot(
            snapshot_id=sid, kind=kind, panel=panel, as_of=at, created_at=datetime.now().replace(microsecond=0),
            inputs=inputs, result=result, report_markdown=report(result, sid),
        )  # fmt: skip
        return self.store.put(snap)

    async def due(self) -> datetime | None:
        """到了检查时刻就返回矿井时间，否则 None。"""
        now = await self.md.now()
        last = self.state.get("last_check")
        if last is not None and now < datetime.fromisoformat(last):  # 矿井时钟倒退（模拟场景被重置）：从头值守
            log.info("矿井时钟倒退（%s → %s），值守状态清零", last, now.isoformat())
            self.state = {"last_check": None, "panels": {}}
            return now
        if last is not None and now - datetime.fromisoformat(last) < timedelta(minutes=self.cfg["interval_min"]):
            return None
        return now

    async def tick(self, now: datetime) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for panel in self.cfg["panels"]:
            events += await self._check_panel(panel, now)
            events += await self._unseal_followup(panel, now)
        self.state["last_check"] = now.isoformat()
        self._save_state()
        return events

    async def _check_panel(self, panel: str, now: datetime) -> list[dict[str, Any]]:
        prev = self.state["panels"].setdefault(panel, {"level": None, "stage": None, "alarms": [], "followups": {}})
        found = await alarms.check_methane(self.md) + await alarms.check_reignition(self.md, panel)
        keys = sorted({f"{a.rule_id}/{a.sensor_id}" for a in found})
        w = await warning.assess(self.md, panel)
        changes = []
        new_alarms = [a for a in found if f"{a.rule_id}/{a.sensor_id}" not in prev["alarms"]]
        if new_alarms:
            changes.append(("alarm", "；".join(f"[{a.article}] {a.sensor_id}：{a.message}" for a in new_alarms)))
        if prev["level"] is not None and w.level.value != prev["level"]:
            changes.append(
                ("level_change", f"预警等级 {warning.NAMES.get(prev['level'], '无')} → {warning.NAMES.get(w.level.value, '无')}")
            )
        if prev["stage"] is not None and w.stage != prev["stage"]:
            changes.append(("stage_change", f"自燃发展阶段 {prev['stage']} → {w.stage}"))
        first = prev["level"] is None
        prev.update(level=w.level.value, stage=w.stage, alarms=keys)
        if not changes and not (first and (found or w.level.value != "none")):
            return []
        if first and not changes:  # 值守刚启动时已有等级或报警，也记一条
            changes.append(("initial", f"值守开始：预警等级 {warning.NAMES.get(w.level.value, '无')}，报警 {len(found)} 条"))
        result = await assess.assess_panel(self.md, panel, self.xc, self.public_url)
        snap = self._save(
            SnapshotKind.ASSESSMENT, panel, result["as_of"], {"panel": panel, "source": "monitor"}, result, reports.assessment
        )
        out = []
        for kind, message in changes:
            e = {
                "mine_time": now.isoformat(), "panel": panel, "kind": kind, "message": message,
                "level": w.level.value, "stage": w.stage, "alarms": [asdict(a) for a in found], "snapshot_id": snap.snapshot_id,
            }  # fmt: skip
            self._emit(e)
            out.append(e)
        return out

    async def _unseal_followup(self, panel: str, now: datetime) -> list[dict[str, Any]]:
        """启封后按班复评（第164条：启封后 3 日内每班检查）。"""
        fu = self.state["panels"].setdefault(panel, {}).setdefault("followups", {})
        out = []
        for area in await self.md.fire_areas():
            if area.panel != panel or area.status != FireAreaStatus.UNSEALED or area.unsealed_at is None:
                continue
            if now - area.unsealed_at > timedelta(hours=self.cfg["unseal_watch_h"]) or now < area.unsealed_at:
                continue
            last = fu.get(area.area_id)
            if last is not None and now - datetime.fromisoformat(last) < timedelta(hours=self.cfg["shift_h"]):
                continue
            fu[area.area_id] = now.isoformat()
            if now - area.unsealed_at < timedelta(hours=1):
                continue  # 刚启封，还没有可比较的数据；下一班再评
            result = await effect.panel_effect(self.md, panel, area.unsealed_at)
            result["since_source"] = f"火区台账：{area.area_id} 启封时间"
            result["escalation_suggested"] = result["overall"] == "worsening"
            snap = self._save(
                SnapshotKind.EFFECT,
                panel,
                result["as_of"],
                {"panel": panel, "source": "monitor-unseal-followup"},
                result,
                reports.effect_review,
            )
            e = {
                "mine_time": now.isoformat(), "panel": panel, "kind": "unseal_followup",
                "message": f"{area.name} 启封后按班复评：{result['overall_name']}", "snapshot_id": snap.snapshot_id,
            }  # fmt: skip
            self._emit(e)
            out.append(e)
        return out


async def run_forever() -> None:
    md = MineData.from_env()
    mon = Monitor(md, SnapshotStore())
    log.info("值守进程启动，工作面 %s，每 %s 矿井分钟检查一次", mon.cfg["panels"], mon.cfg["interval_min"])
    while True:
        try:
            now = await mon.due()
            if now is not None:
                await mon.tick(now)
        except Exception as exc:  # noqa: BLE001  mine_data 暂时不可用等：记日志，下一轮再试，值守不能退出
            log.warning("值守检查失败：%s", exc)
        await asyncio.sleep(mon.cfg["poll_s"])


def main() -> None:
    logging.basicConfig(level=os.environ.get("RESCUE_LOG_LEVEL", "INFO"))
    for noisy in ("httpx", "httpx2", "httpcore", "mcp"):  # 每次取数一条请求日志，值守日志里只留事件和告警
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
