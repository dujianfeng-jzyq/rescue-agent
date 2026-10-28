"""mine-data 服务（模拟矿井版）。

对智能体暴露的 MCP 工具就是数据契约规定的接口；真实矿井适配器必须实现同名、同参数、同返回结构的工具
（由 mcp/contract/conformance 下的一致性测试检验）。

另有 /sim/* 控制接口，供演示控制台、评测脚本和 actions 服务（模拟处置执行）使用。
它们不是 MCP 工具，智能体看不到；真实矿井适配器也没有这些接口。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from mcp.server.mcpserver import MCPServer
from rescue_contract.models import (
    Measurand,
    MineClock,
    MineEvent,
    MineNetwork,
    PersonLocation,
    Reading,
    RescueTeam,
    ResourceItem,
    SensorPoint,
)
from starlette.requests import Request
from starlette.responses import JSONResponse

from .engine import SimEngine, all_sensor_ids
from .mine import INVENTORY, NETWORK, RESCUE_TEAMS, SENSOR_BY_ID, SENSORS
from .scenarios import load_all

INSTRUCTIONS = (
    "矿井实时数据服务。提供通风网络、测点清单、监测数据（含数据质量标记）、报警事件、井下人员位置、"
    "应急物资和救援力量台账。所有时间以 get_clock 返回的矿井当前时间为准，不要使用系统时间。"
    "数据质量为 missing 或 suspect 的读数不能作为研判依据，需在结论中说明数据缺失。"
)


class SimHolder:
    def __init__(self, scenario_id: str | None = None, manual_clock: bool = False) -> None:
        self.scenarios = load_all()
        self.reset(scenario_id or os.environ.get("RESCUE_SIM_SCENARIO", "s2-slow-oxidation"), manual_clock)

    def reset(self, scenario_id: str, manual_clock: bool = False) -> None:
        if scenario_id not in self.scenarios:
            raise KeyError(f"未知场景：{scenario_id}，可选：{sorted(self.scenarios)}")
        self.engine = SimEngine(self.scenarios[scenario_id], manual_clock=manual_clock)


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
            raise ValueError(f"未知测点：{bad}")
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
            raise ValueError(f"未知测点：{bad}")
        return sim.engine.latest(ids)

    @server.tool()
    def get_events(hours: float = 24) -> list[MineEvent]:
        """最近若干小时的报警和测点故障事件。"""
        e = sim.engine
        return e.events(e.now_h() - hours, e.now_h())

    @server.tool()
    def get_personnel() -> list[PersonLocation]:
        """井下人员实时位置（人员定位系统）。已升井人员不在列表中。"""
        return sim.engine.personnel()

    @server.tool()
    def get_inventory() -> list[ResourceItem]:
        """应急物资台账：类型、数量、存放位置（网络节点）。"""
        return INVENTORY

    @server.tool()
    def get_rescue_teams() -> list[RescueTeam]:
        """救援力量台账：救护队、人数、驻地、响应时间。"""
        return RESCUE_TEAMS

    # ── /sim/* 控制接口（不是 MCP 工具）──────────────────────────────────

    @server.custom_route("/sim/state", methods=["GET"])
    async def sim_state(_req: Request) -> JSONResponse:
        e = sim.engine
        return JSONResponse(
            {
                "scenario": {"id": e.scenario.id, "name": e.scenario.name, "duration_h": e.scenario.duration_h},
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

    @server.custom_route("/sim/interventions", methods=["POST"])
    async def sim_intervene(req: Request) -> JSONResponse:
        """模拟处置被执行。actions 服务在指令获批后调用（或由演示控制台手工调用）。"""
        body = await req.json()
        iv = sim.engine.apply(body["type"], body.get("params", {}), body.get("roadways", []), body.get("source", "control"))
        return JSONResponse({"ok": True, "applied_at": sim.engine.at(iv.at_h).isoformat()})

    return server


def window_default(now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(hours=24), now
