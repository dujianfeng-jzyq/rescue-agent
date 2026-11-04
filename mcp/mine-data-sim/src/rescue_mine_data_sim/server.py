"""mine-data 服务（模拟矿井版）。

对智能体暴露的 MCP 工具就是数据契约规定的接口；真实矿井适配器必须实现同名、同参数、同返回结构的工具
（由 mcp/contract/conformance 下的一致性测试检验）。

另有 /sim/* 控制接口，供演示控制台、评测脚本和 actions 服务（模拟处置执行）使用。
它们不是 MCP 工具，智能体看不到；真实矿井适配器也没有这些接口。

演示模式（RESCUE_SIM_MODE=demo，docs/design-v2.md 4.10）：启动即按常态（s1-normal 的参数）连续运行、没有终点；
/sim/demo/inject 把事故场景接到当前矿井时间上；/sim/overrides 设置单个测点的读数（只改读数，不改物理模型）；
设置了 RESCUE_RECORD_DIR 时只读地看记录目录，出现 anomaly 为真的检测记录就自动暂停（只为演示，不是矿井接口的一部分）。
演示控制台在 /console（页面本身不需要令牌，调用 /sim/ 接口时带控制令牌）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from rescue_contract.models import (
    FireArea,
    Measurand,
    MineClock,
    MineEvent,
    MineNetwork,
    PersonnelSnapshot,
    Reading,
    RescueTeam,
    ResourceItem,
    SensorPoint,
)
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from .engine import SimEngine, all_sensor_ids
from .mine import INVENTORY, NETWORK, RESCUE_TEAMS, SENSOR_BY_ID, SENSORS
from .scenarios import load_all

INSTRUCTIONS = (
    "矿井实时数据服务。提供通风网络、测点清单、监测数据（含数据质量标记）、报警事件、井下人员位置、"
    "应急物资和救援力量台账。所有时间以 get_clock 返回的矿井当前时间为准，不要使用系统时间。"
    "数据质量为 missing 或 suspect 的读数不能作为研判依据，需在结论中说明数据缺失。"
)


NORMAL = "s1-normal"


class SimHolder:
    def __init__(self, scenario_id: str | None = None, manual_clock: bool = False, mode: str | None = None) -> None:
        self.scenarios = load_all()
        self.mode = "scenario"
        self.auto_pause = True
        self.anomalies: list[dict] = []
        if (mode or os.environ.get("RESCUE_SIM_MODE", "scenario")) == "demo":
            self.reset_normal(float(os.environ.get("RESCUE_SIM_SPEED", "60")))
        else:
            self.reset(scenario_id or os.environ.get("RESCUE_SIM_SCENARIO", "s2-slow-oxidation"), manual_clock)

    def reset(self, scenario_id: str, manual_clock: bool = False) -> None:
        if scenario_id not in self.scenarios:
            raise KeyError(f"未知场景：{scenario_id}，可选：{sorted(self.scenarios)}")
        self.engine = SimEngine(self.scenarios[scenario_id], manual_clock=manual_clock)
        self.mode = "scenario"
        self.anomalies = []

    def reset_normal(self, speed: float | None = None) -> None:
        """演示：回到常态连续运行（s1-normal 的参数，没有终点）。"""
        self.engine = SimEngine(self.scenarios[NORMAL], continuous=True)
        if speed is not None:
            self.engine.clock.set_speed(speed)
        self.mode = "demo"
        self.anomalies = []
        self.resumed_wall = time.time()


class RecordWatcher(threading.Thread):
    """只读地看记录目录：出现 anomaly 为真的检测记录（fire-assessment/check）就暂停模拟时钟（演示的自动暂停）。

    只看本次运行（或上次继续）之后写入的记录；不是矿井接口的一部分，真实矿井适配器不读记录。"""

    def __init__(self, sim: SimHolder, directory: Path, poll_s: float = 2.0) -> None:
        super().__init__(daemon=True, name="record-watcher")
        self.sim, self.dir, self.poll_s = sim, directory, poll_s
        self.seen: set[str] = set()

    def scan(self) -> list[dict]:
        found = []
        since = getattr(self.sim, "resumed_wall", 0.0)
        for path in sorted(self.dir.glob("RS-*.json")) if self.dir.is_dir() else []:
            if path.name in self.seen:
                continue
            self.seen.add(path.name)
            try:
                if path.stat().st_mtime < since:
                    continue
                rec = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            src, res = rec.get("source") or {}, ((rec.get("content") or {}).get("result") or {}) if isinstance(rec.get("content"), dict) else {}
            if src.get("skill") == "fire-assessment" and src.get("script") == "check" and res.get("anomaly") is True:
                found.append({"record_id": rec.get("record_id"), "panel": rec.get("panel"), "as_of": rec.get("as_of"), "reasons": res.get("reasons", []), "trigger": (rec.get("origin") or {}).get("trigger")})
        return found

    def run(self) -> None:
        while True:
            try:
                hits = self.scan()
                if hits and self.sim.mode == "demo":
                    for h in hits:
                        h["paused"] = bool(self.sim.auto_pause and not self.sim.engine.clock.paused)
                        if h["paused"]:
                            self.sim.engine.clock.set_paused(True)
                        self.sim.anomalies.append(h)
            except Exception:  # noqa: BLE001  看门狗线程不能退出
                pass
            time.sleep(self.poll_s)


def _unknown_sensors(ids: list[str]) -> list[str]:
    return [i for i in ids if i not in SENSOR_BY_ID]


def build_server(holder: SimHolder | None = None) -> MCPServer:
    sim = holder or SimHolder()
    server = MCPServer("mine_data", instructions=INSTRUCTIONS)

    @server.tool()
    def get_clock() -> MineClock:
        """返回矿井当前时间。研判时一律以此为“现在”。"""
        return MineClock(now=sim.engine.now())

    @server.tool()
    def get_network() -> MineNetwork:
        """返回通风网络：节点（含井口、避难硐室）和巷道（风向为 from_node→to_node，含长度、风阻、设计风量）。"""
        return NETWORK

    @server.tool()
    def list_sensors() -> list[SensorPoint]:
        """返回全部测点：来源（安全监控、束管、测温）、可测参数、所在巷道和采空区深度。"""
        return SENSORS

    @server.tool()
    def get_readings(
        sensor_ids: list[str],
        start: str | None = None,
        end: str | None = None,
        measurands: list[Measurand] | None = None,
    ) -> list[Reading]:
        """查询测点历史读数。start/end 为 ISO 8601 时间，缺省为最近 24 小时。
        安全监控 15 分钟一个点，束管 1 小时，测温 30 分钟。单次最多返回 5000 条。"""
        bad = _unknown_sensors(sensor_ids)
        if bad:
            raise ToolError(f"未知测点：{bad}")
        e = sim.engine
        end_h = e.hours_of(datetime.fromisoformat(end)) if end else e.now_h()
        start_h = e.hours_of(datetime.fromisoformat(start)) if start else end_h - 24
        return e.readings(sensor_ids, start_h, end_h, measurands)

    @server.tool()
    def get_latest(sensor_ids: list[str] | None = None) -> list[Reading]:
        """各测点的最新读数。sensor_ids 缺省为全部测点。"""
        ids = sensor_ids or all_sensor_ids()
        bad = _unknown_sensors(ids)
        if bad:
            raise ToolError(f"未知测点：{bad}")
        return sim.engine.latest(ids)

    @server.tool()
    def get_events(hours: float = 24) -> list[MineEvent]:
        """最近若干小时安全监控系统自身产生的事件：测点超报警值、测点故障（无数据）。

        这是矿井监控系统的记录，不包含 fire_model 计算出的红线报警（check_realtime_alarms）；
        后者没有出现在这里是正常的，不代表报警未留痕。"""
        e = sim.engine
        return e.events(e.now_h() - hours, e.now_h())

    @server.tool()
    def get_fire_areas() -> list[FireArea]:
        """火区管理台账：封闭过的火区、所在工作面、状态（sealed 已封闭 / unsealed 已启封）、封闭和启封时间。
        从未封闭的发火区域不在台账里。"""
        return sim.engine.fire_areas()

    @server.tool()
    def get_personnel() -> PersonnelSnapshot:
        """井下人员实时位置（人员定位系统）。已升井人员不在名单中。
        井下总人数、各班组人数、各巷道人数照抄 total、by_team、by_roadway，不要自己数名单。"""
        return PersonnelSnapshot.of(sim.engine.personnel())

    @server.tool()
    def get_inventory() -> list[ResourceItem]:
        """应急物资台账：类型、数量、存放位置（网络节点）。"""
        return INVENTORY

    @server.tool()
    def get_rescue_teams() -> list[RescueTeam]:
        """救援力量台账：救护队、人数、驻地、响应时间。"""
        return RESCUE_TEAMS

    # ── /sim/* 控制接口（不是 MCP 工具）──────────────────────────────────

    record_dir = os.environ.get("RESCUE_RECORD_DIR")
    if holder is None and record_dir:
        RecordWatcher(sim, Path(record_dir)).start()

    @server.custom_route("/sim/state", methods=["GET"])
    async def sim_state(_req: Request) -> JSONResponse:
        e = sim.engine
        return JSONResponse(
            {
                "mode": sim.mode,
                "injected": e.injected,
                "auto_pause": sim.auto_pause,
                "anomalies": sim.anomalies,
                "overrides": [
                    {"sensor_id": o.sensor_id, "measurand": o.measurand.value if o.measurand else None, "mode": o.mode, "value": o.value,
                     "minutes": o.minutes, "fault": o.fault, "from": e.at(o.from_h).isoformat(), "until": None if o.until_h is None else e.at(o.until_h).isoformat()}
                    for o in e.overrides
                ],  # fmt: skip
                "script": [
                    {"type": iv.type, "at": e.at(iv.at_h).isoformat(), "params": iv.params, "applied": iv.at_h <= e.now_h() + 1e-9}
                    for iv in e._interventions if iv.source == "script"
                ],
                "scenario": {"id": e.scenario.id, "name": e.scenario.name, "duration_h": None if e.continuous else e.scenario.duration_h},
                "now": e.now().isoformat(),
                "elapsed_h": round(e.now_h(), 3),
                "speed": e.clock.speed,
                "paused": e.clock.paused,
                "truth": e.hotspot(),
                "actions": [
                    {"type": iv.type, "at": e.at(iv.at_h).isoformat(), "params": iv.params, "source": iv.source} for iv in e.action_log
                ],
            }
        )

    @server.custom_route("/sim/scenarios", methods=["GET"])
    async def sim_scenarios(_req: Request) -> JSONResponse:
        return JSONResponse([{"id": s.id, "name": s.name, "description": s.description} for s in sim.scenarios.values()])

    @server.custom_route("/sim/scenario", methods=["POST"])
    async def sim_reset(req: Request) -> JSONResponse:
        body = await req.json()
        try:
            sim.reset(body["id"], bool(body.get("manual", False)))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "scenario": body["id"], "now": sim.engine.now().isoformat()})

    @server.custom_route("/sim/speed", methods=["POST"])
    async def sim_speed(req: Request) -> JSONResponse:
        body = await req.json()
        sim.engine.clock.set_speed(float(body["speed"]))
        return JSONResponse({"ok": True, "speed": sim.engine.clock.speed})

    @server.custom_route("/sim/pause", methods=["POST"])
    async def sim_pause(req: Request) -> JSONResponse:
        body = await req.json()
        sim.engine.clock.set_paused(bool(body.get("paused", True)))
        return JSONResponse({"ok": True, "paused": sim.engine.clock.paused})

    @server.custom_route("/sim/advance", methods=["POST"])
    async def sim_advance(req: Request) -> JSONResponse:
        body = await req.json()
        sim.engine.clock.advance(float(body["hours"]))
        return JSONResponse({"ok": True, "now": sim.engine.now().isoformat()})

    # ── 演示（RESCUE_SIM_MODE=demo 或 /sim/demo/normal 进入）──

    def bad(msg: str) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=400)

    @server.custom_route("/sim/demo/normal", methods=["POST"])
    async def demo_normal(req: Request) -> JSONResponse:
        """回到常态连续运行（清空历史、读数设置和事故注入）。"""
        body = await req.json() if (await req.body()) else {}
        sim.reset_normal(float(body["speed"]) if body.get("speed") is not None else float(os.environ.get("RESCUE_SIM_SPEED", "60")))
        return JSONResponse({"ok": True, "now": sim.engine.now().isoformat(), "speed": sim.engine.clock.speed})

    @server.custom_route("/sim/demo/inject", methods=["POST"])
    async def demo_inject(req: Request) -> JSONResponse:
        """开始演示：把事故场景接到当前矿井时间上（只在常态连续运行时可用），可同时改倍速。

        advance_h：注入后立即快进这么多矿井小时（物理状态照常逐步计算）。持续检测下一次检测就看到快进之后的状态，
        与基线比较一次报告（等级、阶段的变化合在一起）；不快进时会在事故发展的第一个变化（如自燃阶段推进）就停下。"""
        body = await req.json()
        if sim.mode != "demo":
            return bad("只有常态连续运行时才能注入事故（先 POST /sim/demo/normal）")
        sc = sim.scenarios.get(body.get("scenario", "s4-escalation"))
        if sc is None or sc.id == NORMAL:
            return bad(f"未知事故场景：{body.get('scenario')}")
        advance_h = float(body.get("advance_h") or 0)
        if not 0 <= advance_h <= 240:
            return bad("advance_h 取 0–240 矿井小时")
        rec = sim.engine.inject(sc)
        if advance_h:
            sim.engine.clock.advance(advance_h)
        if body.get("speed") is not None:
            sim.engine.clock.set_speed(float(body["speed"]))
        return JSONResponse({"ok": True, "injected": rec, "advanced_h": advance_h, "now": sim.engine.now().isoformat(), "speed": sim.engine.clock.speed})

    @server.custom_route("/sim/demo/resume", methods=["POST"])
    async def demo_resume(req: Request) -> JSONResponse:
        """继续（自动暂停之后）：之后写入的异常检测记录才会再次触发暂停。可同时改倍速。"""
        body = await req.json() if (await req.body()) else {}
        if body.get("speed") is not None:
            sim.engine.clock.set_speed(float(body["speed"]))
        sim.resumed_wall = time.time()
        sim.engine.clock.set_paused(False)
        return JSONResponse({"ok": True, "now": sim.engine.now().isoformat(), "speed": sim.engine.clock.speed})

    @server.custom_route("/sim/demo/auto-pause", methods=["POST"])
    async def demo_auto_pause(req: Request) -> JSONResponse:
        sim.auto_pause = bool((await req.json()).get("enabled", True))
        return JSONResponse({"ok": True, "auto_pause": sim.auto_pause})

    @server.custom_route("/sim/overrides", methods=["POST"])
    async def sim_override(req: Request) -> JSONResponse:
        """设置单个测点的读数：{sensor_id, measurand?, mode: follow|fixed|offset|ramp|fault, value?, minutes?, fault?}。"""
        body = await req.json()
        try:
            m = Measurand(body["measurand"]) if body.get("measurand") else None
            o = sim.engine.set_override(
                str(body["sensor_id"]), m, str(body.get("mode", "follow")),
                float(body["value"]) if body.get("value") is not None else None,
                float(body["minutes"]) if body.get("minutes") is not None else None, body.get("fault"),
            )  # fmt: skip
        except (KeyError, ValueError) as exc:
            return bad(str(exc))
        return JSONResponse({"ok": True, "active": o is not None, "at": sim.engine.now().isoformat()})

    @server.custom_route("/sim/series", methods=["GET"])
    async def sim_series(req: Request) -> JSONResponse:
        """控制台曲线：?sensor_id=&measurand=&hours=6，每个采样点的模型值、输出值、是否人工设置。"""
        q = req.query_params
        sid = q.get("sensor_id", "")
        if sid not in SENSOR_BY_ID:
            return bad(f"未知测点：{sid}")
        try:
            m = Measurand(q.get("measurand", ""))
        except ValueError:
            return bad("measurand 不合法")
        e = sim.engine
        hours = min(float(q.get("hours", 6)), 72)
        return JSONResponse({"sensor_id": sid, "measurand": m.value, "points": e.series(sid, m, e.now_h() - hours, e.now_h())})

    @server.custom_route("/sim/sensors", methods=["GET"])
    async def sim_sensors(_req: Request) -> JSONResponse:
        return JSONResponse([{"id": x.id, "name": x.name, "source": x.source.value, "measurands": [m.value for m in x.measurands]} for x in SENSORS])

    @server.custom_route("/console", methods=["GET"])
    async def console(_req: Request) -> HTMLResponse:
        return HTMLResponse((Path(__file__).parent / "console.html").read_text(encoding="utf-8"))

    @server.custom_route("/sim/interventions", methods=["POST"])
    async def sim_intervene(req: Request) -> JSONResponse:
        """模拟处置被执行。actions 服务在指令获批后调用（或由演示控制台手工调用）。"""
        body = await req.json()
        iv = sim.engine.apply(body["type"], body.get("params", {}), body.get("roadways", []), body.get("source", "control"))
        return JSONResponse({"ok": True, "applied_at": sim.engine.at(iv.at_h).isoformat()})

    return server


def window_default(now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(hours=24), now
