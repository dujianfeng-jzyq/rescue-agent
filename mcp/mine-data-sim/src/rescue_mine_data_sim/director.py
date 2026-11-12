"""演示导演（第五版，docs/design-v5-demo-flow.md）：控制台点一次“开始演示”，按预警等级分段推进，到每一级暂停等操作员选处置，
按所选操作换剧本继续，走完自动结束。

时间：暂停就是冻结时钟（不再自由运行）。推进由导演一步一步做：先一次跳到离该等级还差一点的地方（jump_h），再按 step_h 一步一步推，
跳的途中有跟踪计划到点就先停在到点时刻，等智能体复评完再跳剩下的（否则跟踪计划会晚十几个小时才投递），
每一步等监听器检测完（心跳的矿井时间追上）再看事件库有没有新的等级告警；这样不会像自由运行那样冲过等级。
每一步之前还要等值班智能体处理完（唤醒插件写的 duty-status.json：会话不忙、事件库里没有待投递的告警）：
否则智能体还在处理上一级（提交、等批准、登记跟踪），矿井已经推进几个小时，下一级告警和跟踪到点一起涌来。结束段同样一步一步等。
等级信号来自事件库里监听器写的告警（“升为 黄色/橙色/红色”），导演不判断预警等级，只读。

分支剧本（`scenarios/demo-flow.yaml`，占位值）：每一级的选项、按“所选操作的个数”走的分支（继续到下一级，或跑一段时间后结束），
操作员在卡片输入框里输入“不采取处置”时（智能体经 record_response_choice 记进事件时间线，矿井什么也不执行）走 `when: {declined: true}` 的分支；
点“跳过”（暂不决定）或选择冲突时继续等，
分支可以改过程参数（`params`/`scale`）。`apply_options: true` 时导演自己把所选操作施加到模拟矿井（控制台代替选项卡片的过渡期）；
智能体经 guard 下发时由 guard 经 rescue-mine 执行，导演就不再重复施加。
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from rescue_contract.response_options import LEVEL_NAMES, ResponseOption, options_for

LEVELS = ("黄色", "橙色", "红色")
LEVEL_RE = re.compile(r"升为\s*(黄色|橙色|红色)")


def level_of(summary: str) -> str | None:
    m = LEVEL_RE.search(summary or "")
    return m.group(1) if m else None


def alert_level(data: dict[str, Any]) -> str | None:
    """告警升到的等级（中文名）：优先读监听器写的结构化字段 level_to，没有（旧告警）再解析摘要文字。
    原来只解析文字，监听器的措辞一改，导演就认不出升级（2026-11-12 代码复查第 5 轮）。"""
    lv = data.get("level_to")
    if isinstance(lv, str):
        return LEVEL_NAMES.get(lv)  # 蓝色等不在演示流程里的等级为 None
    return level_of(str(data.get("summary", "")))


def load_flow(path: Path) -> dict[str, Any]:
    flow = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_flow(flow)
    return flow


def validate_flow(flow: dict[str, Any]) -> None:
    for key in ("incident", "stages"):
        if key not in flow:
            raise ValueError(f"演示流程缺少 {key}")
    stages = flow["stages"]
    for name, st in stages.items():
        if name not in LEVELS:
            raise ValueError(f"阶段名必须是预警等级：{name}")
        if "options" in st:
            raise ValueError(f"{name}：选项取自处置选项表（rescue_contract.response_options），不在流程文件里写")
        if not options_for(name):
            raise ValueError(f"{name} 在处置选项表里没有选项")
        if not st.get("branches"):
            raise ValueError(f"{name} 没有分支")
        for b in st["branches"]:
            if (b.get("when") or {}).get("declined") and "again" in b:
                raise ValueError(f"{name} 的“不采取处置”分支不能是 again（会一直等）")
            if sum(k in b for k in ("goto", "end", "again")) != 1:
                raise ValueError(f"{name} 的分支要么 goto、要么 end、要么 again（追加处置）")
            if "goto" in b and b["goto"] not in stages:
                raise ValueError(f"{name} 的分支指向不存在的阶段：{b['goto']}")
            if "end" in b and "run_h" not in b:
                raise ValueError(f"{name} 的结束分支要写 run_h")


class Director:
    """状态：idle（常态，没开始）、running（推进中等某级告警）、holding（暂停，等操作员选）、ending（跑结束段）、finished。"""

    def __init__(
        self, sim, flow: dict[str, Any], store=None, heartbeat: Path | None = None, wait_s: float = 12.0, settle_s: float | None = None,
        duty: Path | None = None, duty_stale_s: float = 20.0,
    ) -> None:  # fmt: skip
        self.sim, self.flow, self.store, self.heartbeat = sim, flow, store, heartbeat
        self.duty, self.duty_stale_s = duty, duty_stale_s  # 值班会话状态（唤醒插件每次轮询写）；没有或过期（没接 DSH）不等
        self.settle_s = float(flow.get("settle_s", 3.0)) if settle_s is None else settle_s  # 认出操作员的选择后静默多久再走分支（真实秒）
        self.wait_s = wait_s  # 等监听器检测一步的上限（真实秒）；没有监听器时超时就继续
        self.lock = threading.RLock()
        self.state = "idle"
        self.stage: str | None = None  # 当前等的/暂停在的等级
        self.log: list[dict[str, Any]] = []
        self.chosen: list[dict[str, Any]] = []
        self._done: list[str] = []  # 本阶段已下发的选项（追加处置时累计）
        self._log_idx = 0  # 进入等待时处置日志的长度：只看之后执行的处置
        self._matched: set[str] = set()  # 等待期间矿井执行的处置对应的选项
        self._last_match = 0.0
        self._hold_wall = 0.0  # 进入等待（或要求追加）的真实时间：只认之后记下的操作员选择
        self._seen_choice: set[str] = set()
        self._seen: set[str] = set()
        self._start_wall = 0.0
        self._phase = "advance"
        self._target: datetime | None = None
        self._deadline = 0.0
        self._jump_left = 0.0  # 本段还要跳的矿井小时（途中遇到跟踪计划到点分几次跳）
        self._end_at_h = 0.0
        self._outcome = ""
        self.waiting: str | None = None  # 正在等什么（给控制台看）

    # ── 对外 ──

    @property
    def active(self) -> bool:
        """演示进行中：时间只由导演推进，控制台的手动注入、继续、快进都不可用。"""
        return self.state in ("running", "holding", "ending")

    def status(self) -> dict[str, Any]:
        with self.lock:
            st = self.flow["stages"].get(self.stage or "") if self.state == "holding" else None
            return {
                "state": self.state,
                "stage": self.stage,
                "options": [{"id": o.id, "label": o.label, "done": o.id in self._done} for o in self._options()] if st else [],
                "prompt": (st or {}).get("prompt"),
                "chosen": self.chosen,
                "outcome": self._outcome,
                "waiting": self.waiting if self.state in ("running", "ending") else None,
                "log": self.log[-12:],
            }

    def reset(self) -> None:
        """矿井被重置（回到常态、换场景）时调用：导演回到待开始，丢掉进行中的演示。"""
        with self.lock:
            self.state, self.stage, self._outcome, self._done, self.chosen = "idle", None, "", [], []
            self.waiting = None
            self.log = []

    def start(self) -> None:
        with self.lock:
            if self.active:
                raise ValueError("演示正在进行")
            if self.sim.mode != "demo":
                raise ValueError("只有演示模式才能开始演示")
            if self.sim.engine.injected:  # 上一次演示的残留：回到常态再开始
                self.sim.reset_normal(self.sim.engine.clock.speed)
            sc = self.sim.scenarios.get(self.flow["incident"])
            if sc is None:
                raise ValueError(f"未知事故场景：{self.flow['incident']}")
            self.sim.engine.inject(sc)
            self.sim.engine.clock.set_paused(True)  # 之后时间只由导演推进
            self.sim.resumed_wall = time.time()
            self._demo_log_idx = len(self.sim.engine.action_log)  # 本次演示从这里开始的处置
            self.chosen, self.log, self._seen, self._outcome, self._done = [], [], set(), "", []
            self._start_wall = time.time()
            self._begin_running("黄色", first=True)
            self._note("开始演示：注入事故，等预警")

    def _options(self) -> tuple[ResponseOption, ...]:
        return options_for(self.stage or "")

    def choose(self, option_ids: list[str]) -> dict[str, Any]:
        """控制台的人工通道：没有智能体时，导演自己把所选操作施加到模拟矿井。"""
        with self.lock:
            if self.state != "holding":
                raise ValueError("现在不在等待选择")
            known = {o.id: o for o in self._options()}
            bad = [i for i in option_ids if i not in known]
            if bad:
                raise ValueError(f"未知选项：{bad}")
            ids = [i for i in dict.fromkeys(option_ids) if i not in self._done]  # 追加处置时已下发的不再重复
            if not ids:
                raise ValueError("没有新的处置可下发")
            for i in ids:
                self.sim.engine.apply(known[i].step, {}, [], "demo-operator")
            return self._decide(ids)

    def _decide(self, ids: list[str], declined: bool = False) -> dict[str, Any]:
        """按累计选中的个数走分支（ids 是这一次新增的选项，已经执行）；declined：操作员输入了“不采取处置”。"""
        st = self.flow["stages"][self.stage]
        known = {o.id: o for o in self._options()}
        total = self._done + ids
        branch = self._pick_branch(st, len(total), declined)
        self.chosen.append({"stage": self.stage, "options": ids, **({"declined": True} if declined else {})})
        names = "、".join(known[i].label for i in ids) or "不采取处置"
        if "again" in branch:
            self._done = total
            self._reset_wait()
            self._note(f"{self.stage}：选择 {names}；{branch.get('note', '效果有限，需要追加处置')}")
            return self.status()
        fx = branch.get("effect") or {}
        if fx:
            self.sim.engine.set_params(fx.get("params"), fx.get("scale"))
        for iv in fx.get("interventions") or []:
            self.sim.engine.apply(iv["type"], dict(iv.get("params") or {}), [], "demo-script")
        self._done = []
        self._note(f"{self.stage}：选择 {names}；{branch.get('note', '')}".rstrip("；"))
        self.sim.resumed_wall = time.time()
        if "goto" in branch:
            self._begin_running(branch["goto"], first=False)
        else:
            self._outcome = branch.get("message", "")
            self._end_at_h = self.sim.engine.now_h() + float(branch["run_h"])
            self.state, self.stage, self._phase = "ending", None, "advance"
        return self.status()

    def _reset_wait(self) -> None:
        self._log_idx = len(self.sim.engine.action_log)
        self._matched, self._last_match = set(), 0.0
        self._hold_wall = time.time()

    def _operator_choices(self) -> list[str]:
        """等待期间智能体记下的操作员选择（事件时间线里 record_response_choice 写的 note，按出现顺序，只给新的）。"""
        if self.store is None:
            return []
        out: list[str] = []
        for inc in self.store.list():
            for e in self.store.entries(inc["incident_id"]):
                d = e.get("data") or {}
                if e["kind"] != "note" or d.get("type") != "response_choice" or e["event_id"] in self._seen_choice:
                    continue
                if LEVEL_NAMES.get(str(d.get("level")), d.get("level")) != self.stage:
                    continue
                try:
                    if datetime.fromisoformat(e["wall"]).timestamp() < self._hold_wall - 1:  # 事件时间戳截到秒
                        continue
                except ValueError:
                    continue
                self._seen_choice.add(e["event_id"])
                out.append(str(d.get("choice")))
        return out

    def _carry_over(self) -> None:
        """进入等待时：必做项（不由操作员选、持续生效的，如黄色的加密监测）在本次演示里已经由 guard 执行过，就算已到位。
        否则智能体在蓝色就开了加密监测、到黄色不再重复下发（这是对的），导演会一直等一个不会再来的处置。
        可选项（橙色的注氮、注浆、堵漏）仍只认进入等待之后执行的：分支按“这一级选了几项”走。"""
        mandatory: dict[str, list[str]] = {}
        for o in self._options():
            if not o.choosable:
                mandatory.setdefault(o.step, []).append(o.id)
        log = self.sim.engine.action_log[getattr(self, "_demo_log_idx", 0) : self._log_idx]
        have = {i for iv in log if iv.source == "action" for i in mandatory.get(iv.type, ())}
        if have:
            self._matched |= have
            self._last_match = time.time()
            self._note(f"{self.stage}：{'、'.join(o.label for o in self._options() if o.id in have)} 已在之前下发，仍在执行")

    def _tick_holding(self) -> None:
        """看矿井执行了哪些处置（guard 经 rescue-mine 下发，来源 action），认出对应的选项；静默 settle_s 秒后按所选个数走分支。"""
        log = self.sim.engine.action_log
        by_step: dict[str, list[str]] = {}
        for o in self._options():
            by_step.setdefault(o.step, []).append(o.id)
        for iv in log[self._log_idx :]:
            if iv.source == "action":
                new = set(by_step.get(iv.type, ())) - set(self._done) - self._matched
                if new:
                    self._matched |= new
                    self._last_match = time.time()
        self._log_idx = len(log)
        if not self._matched:
            for choice in self._operator_choices():
                if choice == "none":
                    self._note(f"{self.stage}：操作员选择不采取处置")
                    self._decide([], declined=True)
                    return
                if choice == "undecided":
                    self._note(f"{self.stage}：操作员暂未选择处置，继续等待（可在对话里要求重新给出处置选项）")
                if choice == "conflict":
                    self._note(f"{self.stage}：操作员的选择互相冲突（勾了处置又输入“不采取处置”），等智能体重问")
        if self._matched and time.time() - self._last_match >= self.settle_s:
            ids = [o.id for o in self._options() if o.id in self._matched]
            self._matched = set()
            self._note(f"{self.stage}：矿井执行了 {'、'.join(o.label for o in self._options() if o.id in ids)}")
            self._decide(ids)

    def tick(self) -> None:
        """导演线程每秒调一次。"""
        with self.lock:
            if self.state == "running":
                self._tick_running()
            elif self.state == "holding":
                self._tick_holding()
            elif self.state == "ending":
                self._tick_ending()

    # ── 内部 ──

    def _note(self, text: str) -> None:
        self.log.append({"at": self.sim.engine.now().isoformat(timespec="seconds"), "text": text})

    @staticmethod
    def _pick_branch(st: dict[str, Any], n: int, declined: bool = False) -> dict[str, Any]:
        if declined:  # 明确不处置：有专门的分支走它，没有按已做的项数
            for b in st["branches"]:
                if (b.get("when") or {}).get("declined"):
                    return b
        for b in st["branches"]:
            w = b.get("when") or {}
            if w.get("declined"):
                continue
            if int(w.get("min", 0)) <= n <= int(w.get("max", 10**6)):
                return b
        raise ValueError(f"选 {n} 项没有对应的分支")

    def _begin_running(self, stage: str, first: bool) -> None:
        self.state, self.stage = "running", stage
        self._phase, self._target = "advance", None
        self._jump_left = float(self.flow["stages"][stage].get("jump_h", 0))
        self.sim.engine.clock.set_paused(True)

    def _heartbeat_mine_now(self) -> datetime | None:
        if self.heartbeat is None:
            return None
        try:
            hb = json.loads(self.heartbeat.read_text(encoding="utf-8"))
            if hb.get("error") or not hb.get("mine_now"):
                return None
            return datetime.fromisoformat(str(hb["mine_now"]))
        except (OSError, ValueError):
            return None

    def _new_levels(self) -> list[str]:
        """事件库里本次演示开始之后监听器写的、未处理过的等级告警。"""
        out: list[str] = []
        if self.store is None:
            return out
        for inc in self.store.list(include_closed=True):
            for e in self.store.entries(inc["incident_id"]):
                if e["kind"] != "alert" or e["event_id"] in self._seen:
                    continue
                self._seen.add(e["event_id"])
                try:
                    if datetime.fromisoformat(e["wall"]).timestamp() < self._start_wall - 1:  # 事件时间戳截到秒
                        continue
                except ValueError:
                    continue
                lv = alert_level(e.get("data") or {})
                if lv:
                    out.append(lv)
        return out

    def _next_followup_in_h(self) -> tuple[float, str] | None:
        """最近一个还没到点的跟踪计划离现在多少矿井小时（及其编号）；没有返回 None。"""
        if self.store is None:
            return None
        now = self.sim.engine.now().replace(tzinfo=None, microsecond=0)
        best: tuple[float, str] | None = None
        for inc in self.store.list():
            for f in inc["followups"]:
                if f.get("state") != "pending":
                    continue
                try:
                    h = (datetime.fromisoformat(str(f["due_at"])[:19]) - now).total_seconds() / 3600
                except (KeyError, ValueError):
                    continue
                if h > 0 and (best is None or h < best[0]):
                    best = (round(h, 4), str(f.get("followup_id", "")))
        return best

    def _agent_busy(self) -> str | None:
        """值班智能体还没处理完时返回原因。没有值班状态文件、文件过期（没接 DSH 或 DSH 停了）按不忙，导演照常推进。"""
        if self.duty is None:
            return None
        try:
            st = json.loads(self.duty.read_text(encoding="utf-8"))
            fresh = time.time() - float(st.get("wall", 0)) <= self.duty_stale_s
        except (OSError, ValueError, TypeError):
            return None
        if not fresh:
            return None
        if st.get("busy"):
            return "等值班智能体处理完"
        if self.store is not None and self.store.pending_alerts():
            return "等告警投递给值班智能体"
        return None

    def _hold_for_agent(self) -> bool:
        self.waiting = self._agent_busy()
        return self.waiting is not None

    def _arm_await(self) -> None:
        self._target = self.sim.engine.now().replace(tzinfo=None, microsecond=0)  # 监听器的心跳截到秒
        self._deadline = time.time() + self.wait_s
        self._phase = "await"

    def _sentinel_caught_up(self) -> bool:
        hb = self._heartbeat_mine_now()
        return (hb is not None and self._target is not None and hb >= self._target) or time.time() >= self._deadline

    def _tick_running(self) -> None:
        eng, st = self.sim.engine, self.flow["stages"][self.stage]
        if self._phase == "advance":
            if self._hold_for_agent():
                return
            if self._jump_left > 1e-6:
                jump, due = self._jump_left, self._next_followup_in_h()
                if due is not None and due[0] < jump:
                    jump = due[0]
                    self._note(f"跳过 {jump:g} 矿井小时，跟踪计划 {due[1]} 到点，等复评完再继续")
                else:
                    self._note(f"跳过 {jump:g} 矿井小时，接近{self.stage}预警")
                self._jump_left -= jump
                eng.clock.advance(jump)
            else:
                eng.clock.advance(float(st.get("step_h", self.flow.get("step_h", 0.5))))
            self._arm_await()
            return
        if not self._sentinel_caught_up():
            return
        want = LEVELS.index(self.stage)
        for lv in self._new_levels():
            if LEVELS.index(lv) >= want:
                self.stage = lv if lv in self.flow["stages"] else self.stage
                self.state, self.waiting = "holding", None
                self._done = []
                self._reset_wait()
                eng.clock.set_paused(True)
                self._note(f"出现{lv}预警，暂停，等操作员选择处置")
                self._carry_over()
                return
        self._phase = "advance"

    def _tick_ending(self) -> None:
        """结束段也一步一步推：每步等监听器检测完、值班智能体处理完（跟踪到点、复评都在这段里发生）。"""
        eng = self.sim.engine
        if self._phase == "await":
            if not self._sentinel_caught_up():
                return
            self._phase = "advance"
        if self._hold_for_agent():
            return
        left = self._end_at_h - eng.now_h()
        if left <= 1e-6:
            eng.clock.set_paused(True)
            self.state, self.waiting = "finished", None
            self._note(f"演示结束：{self._outcome}" if self._outcome else "演示结束")
            return
        eng.clock.advance(min(left, float(self.flow.get("end_step_h", 2.0))))
        self._arm_await()


class DirectorThread(threading.Thread):
    def __init__(self, director: Director, period_s: float = 1.0) -> None:
        super().__init__(daemon=True, name="demo-director")
        self.director, self.period_s = director, period_s

    def run(self) -> None:
        while True:
            try:
                self.director.tick()
            except Exception as exc:  # noqa: BLE001  导演线程不能退出
                self.director._note(f"导演出错：{exc}")
            time.sleep(self.period_s)
