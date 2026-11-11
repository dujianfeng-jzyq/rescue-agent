"""mine-data 服务（模拟矿井版）。

对智能体暴露的 MCP 工具就是数据契约规定的接口；真实矿井适配器必须实现同名、同参数、同返回结构的工具
（由 mcp/contract/conformance 下的一致性测试检验）。

另有 /sim/* 控制接口，供演示控制台、评测脚本和 actions 服务（模拟处置执行）使用。
它们不是 MCP 工具，智能体看不到；真实矿井适配器也没有这些接口。

演示模式（RESCUE_SIM_MODE=demo，docs/design-v2.md 4.10）：启动即按常态（s1-normal 的参数）连续运行、没有终点；
/sim/demo/inject 把事故场景接到当前矿井时间上；/sim/overrides 设置单个测点的读数（只改读数，不改物理模型）；
设置了 RESCUE_RECORD_DIR 时只读地看记录目录，出现 anomaly 为真的检测记录就自动暂停；设置了 RESCUE_INCIDENT_DIR 时只读地看事件库，
监听器写出新的告警也自动暂停，并在 /sim/state 里给出事件和监听器心跳供控制台显示（只为演示，不是矿井接口的一部分）。
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
from rescue_contract.incidents import IncidentStore
from rescue_contract.interface import ALL_TOOLS, FEATURES, INTERFACE_VERSION
from rescue_contract.models import (
    CONTRACT_VERSION,
    ActionKind,
    ActionStatus,
    AlarmLevel,
    AlarmStatus,
    DispatchRequest,
    EquipmentState,
    EquipmentStatus,
    FireArea,
    Measurand,
    MineAlarm,
    MineCapabilities,
    MineClock,
    MineDescription,
    MineEvent,
    MineNetwork,
    MineReceipt,
    NoticeRequest,
    PersonnelSnapshot,
    Reading,
    RescueTeam,
    ResourceItem,
    SensorPoint,
    StepType,
    TreatmentRequest,
)
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from .director import Director, DirectorThread, load_flow
from .engine import SimEngine, all_sensor_ids
from .mine import EQUIPMENT, INVENTORY, MINE_ID, MINE_NAME, NETWORK, PANELS, RESCUE_TEAMS, SENSOR_BY_ID, SENSORS
from .scenarios import load_all, scenario_dir

INSTRUCTIONS = (
    "rescue-mine 接口（模拟矿井实现）。提供接口自描述、通风网络、测点清单（含语义角色）、监测数据（含数据质量标记）、"
    "报警事件和矿井报警、设备状态、井下人员位置、应急物资和救援力量台账，以及通知、调度、处置的执行与回执。"
    "所有时间以 get_clock 返回的矿井当前时间为准，不要使用系统时间。"
    "数据质量为 missing 或 suspect 的读数不能作为研判依据，需在结论中说明数据缺失。"
)


NORMAL = "s1-normal"

# 模拟：调度“执行中”多久（矿井分钟）后算完成，用来体现异步回执
DISPATCH_RUN_MIN = 30
# 模拟矿井能执行的处置类型（其余返回 rejected 回执）
SIM_TREATMENTS = {
    StepType.INERT_INJECTION,
    StepType.GROUTING,
    StepType.INHIBITOR_SPRAY,
    StepType.LEAK_SEALING,
    StepType.PRESSURE_BALANCING,
    StepType.TEMPORARY_SEAL,
    StepType.PERMANENT_SEAL,
    StepType.UNSEAL,
    StepType.EVACUATE,
    StepType.MONITOR,  # 检查、观测、断电：模拟矿井只记录（进 action_log），不改变物理模型
    StepType.POWER_CUTOFF,
}


class SimHolder:
    def __init__(self, scenario_id: str | None = None, manual_clock: bool = False, mode: str | None = None) -> None:
        self.scenarios = load_all()
        self.mode = "scenario"
        self.auto_pause = True
        self.anomalies: list[dict] = []
        self.clear_actions()
        if (mode or os.environ.get("RESCUE_SIM_MODE", "scenario")) == "demo":
            self.reset_normal(float(os.environ.get("RESCUE_SIM_SPEED", "60")))
        else:
            self.reset(scenario_id or os.environ.get("RESCUE_SIM_SCENARIO", "s2-slow-oxidation"), manual_clock)

    def clear_actions(self) -> None:
        """通知、指令的回执和报警确认状态（重置场景时清空）。"""
        self.receipts: dict[str, MineReceipt] = {}
        self.by_key: dict[str, str] = {}
        self.notices: list[dict] = []
        self.dispatches: list[dict] = []
        self.acked: dict[str, datetime] = {}

    def reset(self, scenario_id: str, manual_clock: bool = False) -> None:
        if scenario_id not in self.scenarios:
            raise KeyError(f"未知场景：{scenario_id}，可选：{sorted(self.scenarios)}")
        self.engine = SimEngine(self.scenarios[scenario_id], manual_clock=manual_clock)
        self.mode = "scenario"
        self.anomalies = []
        self.clear_actions()

    def reset_normal(self, speed: float | None = None) -> None:
        """演示：回到常态连续运行（s1-normal 的参数，没有终点）。"""
        self.engine = SimEngine(self.scenarios[NORMAL], continuous=True)
        if speed is not None:
            self.engine.clock.set_speed(speed)
        self.mode = "demo"
        self.anomalies = []
        self.clear_actions()
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


class IncidentWatcher(threading.Thread):
    """只读地看事件库：监听器写出新的告警（alert）就自动暂停模拟时钟（演示的自动暂停，与 RecordWatcher 同一机制）。"""

    def __init__(self, sim: SimHolder, store: IncidentStore, poll_s: float = 2.0) -> None:
        super().__init__(daemon=True, name="incident-watcher")
        self.sim, self.store, self.poll_s = sim, store, poll_s
        self.seen: set[str] = set()
        self.primed = False

    def scan(self) -> list[dict]:
        found = []
        since = getattr(self.sim, "resumed_wall", 0.0)
        for inc in self.store.list(include_closed=True):
            try:
                entries = self.store.entries(inc["incident_id"])
            except Exception:  # noqa: BLE001
                continue
            for e in entries:
                if e["kind"] != "alert" or e["event_id"] in self.seen:
                    continue
                self.seen.add(e["event_id"])
                if not self.primed:
                    continue  # 启动前就有的告警不触发暂停
                try:
                    if datetime.fromisoformat(e["wall"]).timestamp() < since:
                        continue
                except ValueError:
                    continue
                found.append({"record_id": e["event_id"], "panel": inc.get("panel") or "全矿", "as_of": e["at"], "reasons": [e["data"].get("summary", "")], "trigger": "sentinel"})
        self.primed = True
        return found

    def run(self) -> None:
        while True:
            try:
                for h in self.scan():
                    if self.sim.mode == "demo":
                        h["paused"] = bool(self.sim.auto_pause and not self.sim.engine.clock.paused)
                        if h["paused"]:
                            self.sim.engine.clock.set_paused(True)
                        self.sim.anomalies.append(h)
            except Exception:  # noqa: BLE001  看门狗线程不能退出
                pass
            time.sleep(self.poll_s)


def _sentinel_status(directory: Path, stale_s: float = 60.0) -> dict:
    """监听器心跳（真实时间）。没有心跳文件 = 监听器没启动；心跳过期或带错误 = 监听中断。"""
    try:
        hb = json.loads((directory / "sentinel" / "heartbeat.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "absent", "detail": "监听器未启动（没有心跳）"}
    age = time.time() - float(hb.get("wall", 0))
    if age > stale_s:
        return {"state": "stale", "detail": f"监听中断：{int(age)} 秒没有心跳", "mine_now": hb.get("mine_now")}
    if hb.get("error"):
        return {"state": "error", "detail": f"监听出错：{hb['error']}", "mine_now": hb.get("mine_now")}
    return {"state": "ok", "detail": "监听中", "mine_now": hb.get("mine_now"), "panels": hb.get("panels", [])}


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

        这是矿井监控系统的记录，不包含 rescue-agent 按红线规则重算出的报警（fire-assessment 的 alarms）；
        后者没有出现在这里是正常的，不代表报警未留痕。矿井报警平台的报警（含确认状态）用 get_alarms。"""
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


    # ── 自描述（rescue-mine 接口，docs/design-v3.md 第 4 节）──

    @server.tool()
    def get_capabilities() -> MineCapabilities:
        """接口版本，以及本矿实际提供哪些工具和可选功能。对接前先调用它；工具和功能以此为准。"""
        return MineCapabilities(
            interface_version=INTERFACE_VERSION,
            contract_version=CONTRACT_VERSION,
            mine_id=MINE_ID,
            implementation="mine-sim",
            tools=sorted(ALL_TOOLS),
            features=list(FEATURES),
        )

    @server.tool()
    def describe_mine() -> MineDescription:
        """本矿有哪些工作面（进回风巷、工作面、采空区、避难硐室分别是哪条巷道），以及测点用到了哪些语义角色。
        技能按角色取数（list_sensors 里每个测点有 panel 和 role），不要写死测点编号。"""
        return MineDescription(mine_id=MINE_ID, name=MINE_NAME, panels=PANELS, sensor_roles=sorted({s.role for s in SENSORS}, key=lambda r: r.value))

    # ── 矿井自带报警与设备 ──

    @server.tool()
    def get_alarms(hours: float = 24, active_only: bool = False) -> list[MineAlarm]:
        """矿井报警平台的报警（拉取）：最近若干小时内越限的测点，含当前是否仍越限、是否已被值班人员确认。
        这是矿井自己的报警，不是 fire-assessment 重算的结果；active_only 只返回仍越限的。"""
        e = sim.engine
        out = []
        for sid, m, raised, cleared, value, limit in e.alarms(e.now_h() - hours, e.now_h()):
            if active_only and cleared is not None:
                continue
            aid = f"AL-{sid}-{m.value}-{raised:%Y%m%d%H%M}"
            ack = sim.acked.get(aid)
            out.append(
                MineAlarm(
                    alarm_id=aid, sensor_id=sid, measurand=m, level=AlarmLevel.ALARM,
                    status=AlarmStatus.CLEARED if cleared is not None else AlarmStatus.ACTIVE,
                    raised_at=raised, cleared_at=cleared, value=value, threshold=limit,
                    acknowledged=ack is not None, acknowledged_at=ack,
                    message=f"{SENSOR_BY_ID[sid].name} {m.value}={value} 超过报警值 {limit}",
                )
            )  # fmt: skip
        return out

    @server.tool()
    def get_equipment_status() -> list[EquipmentStatus]:
        """风机、水泵、制氮、注浆设备的运行状态。模拟矿井里制氮机组、注浆站在处置（注惰、灌浆）被执行后转为运行。"""
        now = sim.engine.now()
        done = {iv.type for iv in sim.engine.action_log}
        out = []
        for eid, name, kind, node in EQUIPMENT:
            state = EquipmentState.RUNNING
            if eid == "EQ-PUMP-1":
                state = EquipmentState.STANDBY
            elif eid == "EQ-N2-1":
                state = EquipmentState.RUNNING if StepType.INERT_INJECTION.value in done else EquipmentState.STANDBY
            elif eid == "EQ-GROUT-1":
                state = EquipmentState.RUNNING if StepType.GROUTING.value in done else EquipmentState.STANDBY
            out.append(EquipmentStatus(equipment_id=eid, name=name, kind=kind, node_id=node, state=state, updated_at=now))
        return out

    # ── 通知与指令：只执行，不判断（红线、记录核对、台账在 rescue-agent 的 guard）──

    node_ids = {n.id for n in NETWORK.nodes}
    roadway_ids = {r.id for r in NETWORK.roadways}
    panel_ids = {p.panel_id for p in PANELS}

    def receipt_for(key: str, kind: ActionKind, status: ActionStatus, detail: str | None) -> MineReceipt:
        now = sim.engine.now()
        r = MineReceipt(action_id=f"MA-{len(sim.receipts) + 1:05d}", kind=kind, status=status, received_at=now, updated_at=now, idempotency_key=key, detail=detail)
        sim.receipts[r.action_id] = r
        sim.by_key[key] = r.action_id
        return r

    def existing(key: str, kind: ActionKind) -> MineReceipt | None:
        """相同幂等 key 重复提交只执行一次，返回同一个回执；key 被别的种类的动作用过则报错。"""
        aid = sim.by_key.get(key)
        if aid is None:
            return None
        r = refreshed(sim.receipts[aid])
        if r.kind != kind:
            raise ToolError(f"幂等 key {key} 已用于另一种动作（{r.kind.value}），请换一个")
        return r

    def refreshed(r: MineReceipt) -> MineReceipt:
        """异步回执：调度“执行中”满 DISPATCH_RUN_MIN 个矿井分钟后完成。"""
        now = sim.engine.now()
        if r.kind == ActionKind.DISPATCH and r.status == ActionStatus.EXECUTING and now - r.received_at >= timedelta(minutes=DISPATCH_RUN_MIN):
            r = r.model_copy(update={"status": ActionStatus.COMPLETED, "updated_at": r.received_at + timedelta(minutes=DISPATCH_RUN_MIN)})
            sim.receipts[r.action_id] = r
        return r

    @server.tool()
    def send_notice(request: NoticeRequest) -> MineReceipt:
        """发送预警、撤人等通知，返回回执。相同 idempotency_key 重复提交只发一次。"""
        if (prior := existing(request.idempotency_key, ActionKind.NOTICE)) is not None:
            return prior
        if request.panel is not None and request.panel not in panel_ids:
            raise ToolError(f"未知工作面：{request.panel}，可选：{sorted(panel_ids)}")
        r = receipt_for(request.idempotency_key, ActionKind.NOTICE, ActionStatus.COMPLETED, f"通知已发送（{request.kind.value}）")
        sim.notices.append({"action_id": r.action_id, "at": r.received_at.isoformat(), **request.model_dump(mode="json")})
        return r

    @server.tool()
    def dispatch_order(request: DispatchRequest) -> MineReceipt:
        """下发物资、人员调度，返回回执。真实矿井异步生效：状态先为 executing，用 get_action_status 查询完成情况。"""
        if (prior := existing(request.idempotency_key, ActionKind.DISPATCH)) is not None:
            return prior
        bad = sorted({n for ln in request.lines for n in (ln.from_node, ln.to_node) if n not in node_ids})
        if bad:
            raise ToolError(f"未知网络节点：{bad}")
        r = receipt_for(request.idempotency_key, ActionKind.DISPATCH, ActionStatus.EXECUTING, f"调度执行中（{len(request.lines)} 项）")
        sim.dispatches.append({"action_id": r.action_id, "at": r.received_at.isoformat(), **request.model_dump(mode="json")})
        return r

    @server.tool()
    def submit_treatment(request: TreatmentRequest) -> MineReceipt:
        """提交处置动作（注惰、灌浆、封闭等），返回回执。模拟矿井里动作作用于物理模型；不支持的类型返回 rejected 回执。
        只执行，不做红线校验。"""
        if (prior := existing(request.idempotency_key, ActionKind.TREATMENT)) is not None:
            return prior
        if request.panel is not None and request.panel not in panel_ids:
            raise ToolError(f"未知工作面：{request.panel}，可选：{sorted(panel_ids)}")
        bad = [x for x in request.roadways if x not in roadway_ids]
        if bad:
            raise ToolError(f"未知巷道：{bad}")
        if request.type not in SIM_TREATMENTS:
            return receipt_for(request.idempotency_key, ActionKind.TREATMENT, ActionStatus.REJECTED, f"模拟矿井不能执行 {request.type.value}")
        sim.engine.apply(request.type.value, dict(request.params), request.roadways, "action")
        return receipt_for(request.idempotency_key, ActionKind.TREATMENT, ActionStatus.COMPLETED, f"{request.type.value} 已执行")

    @server.tool()
    def get_action_status(action_id: str) -> MineReceipt:
        """按回执编号查询通知、调度、处置的执行状态（异步生效的动作用它跟踪）。"""
        r = sim.receipts.get(action_id)
        if r is None:
            raise ToolError(f"未知回执编号：{action_id}")
        return refreshed(r)

    # ── /sim/* 控制接口（不是 MCP 工具）──────────────────────────────────

    record_dir = os.environ.get("RESCUE_RECORD_DIR")
    if holder is None and record_dir:
        RecordWatcher(sim, Path(record_dir)).start()

    incident_dir = os.environ.get("RESCUE_INCIDENT_DIR")
    incidents = IncidentStore(incident_dir) if incident_dir else None
    if holder is None and incidents is not None:
        IncidentWatcher(sim, incidents).start()

    def incident_state() -> dict:
        if incidents is None:
            return {"enabled": False}
        rows = []
        for v in incidents.list(include_closed=True)[-20:]:
            try:
                entries = incidents.entries(v["incident_id"])
            except Exception:  # noqa: BLE001
                continue
            want = set(v["pending_alerts"])
            rows.append({**v, "unconfirmed": [{"event_id": x["event_id"], "at": x["at"], "summary": x["data"].get("summary", "")} for x in entries if x["event_id"] in want],
                         "timeline": [{"at": x["at"], "kind": x["kind"], "by": x["by"], "text": x["data"].get("summary") or x["data"].get("reason") or x["data"].get("text") or x["data"].get("expect") or ""} for x in entries[-12:]]})  # fmt: skip
        return {"enabled": True, "sentinel": _sentinel_status(incidents.dir), "incidents": rows}

    @server.custom_route("/sim/state", methods=["GET"])
    async def sim_state(_req: Request) -> JSONResponse:
        e = sim.engine
        return JSONResponse(
            {
                "incident_store": incident_state(),
                "mode": sim.mode,
                "director": director.status() if director else None,
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
            if director is not None:
                director.reset()
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
        if not body.get("paused", True) and (busy := directed()) is not None:
            return busy
        sim.engine.clock.set_paused(bool(body.get("paused", True)))
        return JSONResponse({"ok": True, "paused": sim.engine.clock.paused})

    def directed() -> JSONResponse | None:
        """演示导演进行中时，时间只由导演推进：手动注入、继续、快进会和导演的分支剧本叠在一起（演示里误点注入 s4 后封闭了又升红）。"""
        if director is not None and director.active:
            return JSONResponse({"error": "演示导演正在推进，不能手动注入、继续或快进；要中止请“重置为常态”"}, status_code=409)
        return None

    @server.custom_route("/sim/advance", methods=["POST"])
    async def sim_advance(req: Request) -> JSONResponse:
        if (busy := directed()) is not None:
            return busy
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
        if director is not None:
            director.reset()
        return JSONResponse({"ok": True, "now": sim.engine.now().isoformat(), "speed": sim.engine.clock.speed})

    @server.custom_route("/sim/demo/inject", methods=["POST"])
    async def demo_inject(req: Request) -> JSONResponse:
        """开始演示：把事故场景接到当前矿井时间上（只在常态连续运行时可用），可同时改倍速。"""
        if (busy := directed()) is not None:
            return busy
        body = await req.json()
        if sim.mode != "demo":
            return bad("只有常态连续运行时才能注入事故（先 POST /sim/demo/normal）")
        sc = sim.scenarios.get(body.get("scenario", "s4-escalation"))
        if sc is None or sc.id == NORMAL:
            return bad(f"未知事故场景：{body.get('scenario')}")
        rec = sim.engine.inject(sc)
        if body.get("speed") is not None:
            sim.engine.clock.set_speed(float(body["speed"]))
        return JSONResponse({"ok": True, "injected": rec, "speed": sim.engine.clock.speed})

    @server.custom_route("/sim/demo/resume", methods=["POST"])
    async def demo_resume(req: Request) -> JSONResponse:
        """继续（自动暂停之后）：之后写入的异常检测记录才会再次触发暂停。可同时改倍速。"""
        if (busy := directed()) is not None:
            return busy
        body = await req.json() if (await req.body()) else {}
        if body.get("speed") is not None:
            sim.engine.clock.set_speed(float(body["speed"]))
        sim.resumed_wall = time.time()
        sim.engine.clock.set_paused(False)
        return JSONResponse({"ok": True, "now": sim.engine.now().isoformat(), "speed": sim.engine.clock.speed})

    flow_path = Path(os.environ.get("RESCUE_DEMO_FLOW", scenario_dir() / "demo" / "flow.yaml"))
    director = (
        Director(
            sim, load_flow(flow_path), incidents,
            incidents.dir / "sentinel" / "heartbeat.json" if incidents else None,
            duty=incidents.dir / "duty-status.json" if incidents else None,  # 唤醒插件写的值班会话状态
        )
        if flow_path.is_file()
        else None
    )  # fmt: skip
    if holder is None and director is not None:
        DirectorThread(director).start()

    @server.custom_route("/sim/demo/director", methods=["GET"])
    async def demo_director(_req: Request) -> JSONResponse:
        return JSONResponse(director.status() if director else {"state": "unavailable"})

    @server.custom_route("/sim/demo/start", methods=["POST"])
    async def demo_start(_req: Request) -> JSONResponse:
        """开始演示：注入事故，由导演按预警等级推进（常态下点一次）。"""
        if director is None:
            return bad("没有演示流程（scenarios/demo/flow.yaml）")
        try:
            director.start()
        except ValueError as exc:
            return bad(str(exc))
        return JSONResponse({"ok": True, **director.status()})

    @server.custom_route("/sim/demo/choose", methods=["POST"])
    async def demo_choose(req: Request) -> JSONResponse:
        """操作员选择处置（选项编号列表）；之后导演按分支继续。"""
        if director is None:
            return bad("没有演示流程")
        body = await req.json()
        try:
            return JSONResponse({"ok": True, **director.choose([str(x) for x in body.get("options", [])])})
        except ValueError as exc:
            return bad(str(exc))

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

    @server.custom_route("/sim/alarms/ack", methods=["POST"])
    async def sim_ack_alarm(req: Request) -> JSONResponse:
        """模拟值班人员在矿井报警平台确认一条报警（演示控制台、评测用）。"""
        body = await req.json()
        sim.acked[str(body["alarm_id"])] = sim.engine.now()
        return JSONResponse({"ok": True})

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
