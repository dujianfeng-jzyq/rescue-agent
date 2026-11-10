"""演示导演（第五版，docs/design-v5-demo-flow.md）：控制台点一次“开始演示”，按预警等级分段推进，到每一级暂停等操作员选处置，
按所选操作换剧本继续，走完自动结束。

时间：暂停就是冻结时钟（不再自由运行）。推进由导演一步一步做：先一次跳到离该等级还差一点的地方（jump_h），再按 step_h 一步一步推，
每一步等监听器检测完（心跳的矿井时间追上）再看事件库有没有新的等级告警；这样不会像自由运行那样冲过等级。
等级信号来自事件库里监听器写的告警（“升为 黄色/橙色/红色”），导演不判断预警等级，只读。

分支剧本（`scenarios/demo-flow.yaml`，占位值）：每一级的选项、按“所选操作的个数”走的分支（继续到下一级，或跑一段时间后结束），
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

LEVELS = ("黄色", "橙色", "红色")
LEVEL_RE = re.compile(r"升为\s*(黄色|橙色|红色)")


def level_of(summary: str) -> str | None:
    m = LEVEL_RE.search(summary or "")
    return m.group(1) if m else None


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
        ids = [o["id"] for o in st["options"]]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{name} 的选项编号重复")
        if not st.get("branches"):
            raise ValueError(f"{name} 没有分支")
        for b in st["branches"]:
            if sum(k in b for k in ("goto", "end", "again")) != 1:
                raise ValueError(f"{name} 的分支要么 goto、要么 end、要么 again（追加处置）")
            if "goto" in b and b["goto"] not in stages:
                raise ValueError(f"{name} 的分支指向不存在的阶段：{b['goto']}")
            if "end" in b and "run_h" not in b:
                raise ValueError(f"{name} 的结束分支要写 run_h")


class Director:
    """状态：idle（常态，没开始）、running（推进中等某级告警）、holding（暂停，等操作员选）、ending（跑结束段）、finished。"""

    def __init__(self, sim, flow: dict[str, Any], store=None, heartbeat: Path | None = None, wait_s: float = 12.0) -> None:
        self.sim, self.flow, self.store, self.heartbeat = sim, flow, store, heartbeat
        self.wait_s = wait_s  # 等监听器检测一步的上限（真实秒）；没有监听器时超时就继续
        self.lock = threading.RLock()
        self.state = "idle"
        self.stage: str | None = None  # 当前等的/暂停在的等级
        self.log: list[dict[str, Any]] = []
        self.chosen: list[dict[str, Any]] = []
        self._done: list[str] = []  # 本阶段已下发的选项（追加处置时累计）
        self._seen: set[str] = set()
        self._start_wall = 0.0
        self._phase = "advance"
        self._target: datetime | None = None
        self._deadline = 0.0
        self._jumped = False
        self._end_at_h = 0.0
        self._outcome = ""

    # ── 对外 ──

    def status(self) -> dict[str, Any]:
        with self.lock:
            st = self.flow["stages"].get(self.stage or "") if self.state == "holding" else None
            return {
                "state": self.state,
                "stage": self.stage,
                "options": [{"id": o["id"], "label": o["label"], "done": o["id"] in self._done} for o in st["options"]] if st else [],
                "prompt": (st or {}).get("prompt"),
                "chosen": self.chosen,
                "outcome": self._outcome,
                "log": self.log[-12:],
            }

    def start(self) -> None:
        with self.lock:
            if self.state in ("running", "holding", "ending"):
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
            self.chosen, self.log, self._seen, self._outcome, self._done = [], [], set(), "", []
            self._start_wall = time.time()
            self._begin_running("黄色", first=True)
            self._note("开始演示：注入事故，等预警")

    def choose(self, option_ids: list[str]) -> dict[str, Any]:
        with self.lock:
            if self.state != "holding":
                raise ValueError("现在不在等待选择")
            st = self.flow["stages"][self.stage]
            known = {o["id"]: o for o in st["options"]}
            bad = [i for i in option_ids if i not in known]
            if bad:
                raise ValueError(f"未知选项：{bad}")
            ids = [i for i in dict.fromkeys(option_ids) if i not in self._done]  # 追加处置时已下发的不再重复
            if not ids:
                raise ValueError("没有新的处置可下发")
            total = self._done + ids
            branch = self._pick_branch(st, len(total))
            if self.flow.get("apply_options", True):
                for i in ids:
                    o = known[i]
                    self.sim.engine.apply(o["step"], dict(o.get("params") or {}), list(o.get("roadways") or []), "demo-operator")
            self.chosen.append({"stage": self.stage, "options": ids})
            names = "、".join(known[i]["label"] for i in ids)
            if "again" in branch:
                self._done = total
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
                self.state, self.stage = "ending", None
            return self.status()

    def tick(self) -> None:
        """导演线程每秒调一次。"""
        with self.lock:
            if self.state == "running":
                self._tick_running()
            elif self.state == "ending":
                self._tick_ending()

    # ── 内部 ──

    def _note(self, text: str) -> None:
        self.log.append({"at": self.sim.engine.now().isoformat(timespec="seconds"), "text": text})

    @staticmethod
    def _pick_branch(st: dict[str, Any], n: int) -> dict[str, Any]:
        for b in st["branches"]:
            w = b.get("when") or {}
            if int(w.get("min", 0)) <= n <= int(w.get("max", 10**6)):
                return b
        raise ValueError(f"选 {n} 项没有对应的分支")

    def _begin_running(self, stage: str, first: bool) -> None:
        self.state, self.stage = "running", stage
        self._phase, self._jumped, self._target = "advance", False, None
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
                lv = level_of((e.get("data") or {}).get("summary", ""))
                if lv:
                    out.append(lv)
        return out

    def _tick_running(self) -> None:
        eng, st = self.sim.engine, self.flow["stages"][self.stage]
        if self._phase == "advance":
            if not self._jumped:
                self._jumped = True
                jump = float(st.get("jump_h", 0))
                if jump > 0:
                    eng.clock.advance(jump)
                    self._note(f"跳过 {jump:g} 矿井小时，接近{self.stage}预警")
            else:
                eng.clock.advance(float(st.get("step_h", self.flow.get("step_h", 0.5))))
            self._target = eng.now().replace(tzinfo=None, microsecond=0)  # 监听器的心跳截到秒
            self._deadline = time.time() + self.wait_s
            self._phase = "await"
            return
        hb = self._heartbeat_mine_now()
        done = hb is not None and self._target is not None and hb >= self._target
        if not done and time.time() < self._deadline:
            return
        want = LEVELS.index(self.stage)
        for lv in self._new_levels():
            if LEVELS.index(lv) >= want:
                self.stage = lv if lv in self.flow["stages"] else self.stage
                self.state = "holding"
                eng.clock.set_paused(True)
                self._note(f"出现{lv}预警，暂停，等操作员选择处置")
                return
        self._phase = "advance"

    def _tick_ending(self) -> None:
        eng = self.sim.engine
        left = self._end_at_h - eng.now_h()
        if left <= 1e-6:
            eng.clock.set_paused(True)
            self.state = "finished"
            self._note(f"演示结束：{self._outcome}" if self._outcome else "演示结束")
            return
        eng.clock.advance(min(left, float(self.flow.get("end_step_h", 2.0))))


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
