"""mine-data 一致性检查：模拟矿井和真实矿井适配器跑同一套检查，全部通过才能替换上线。

用法：
    python -m rescue_contract.conformance http://127.0.0.1:8101/mcp [--with-actions]
测试里也可以传入进程内的 MCPServer 实例。

按 rescue-mine 接口表（rescue_contract.interface）检查：必需工具齐全、get_capabilities 如实说明本矿提供的工具和功能、
describe_mine 的工作面拓扑和测点角色与网络、测点清单一致、报警和设备（如提供）的数据合理。
写入类工具（通知、调度、处置）的检查会真的执行动作，只在 --with-actions（或测试里 with_actions=True）时运行：
对模拟矿井安全，对真实矿井不要打开。
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from .client import MineData, MineDataError, connect
from .interface import (
    ACTION_FEATURES,
    ALL_TOOLS,
    FEATURE_ALARMS,
    FEATURE_DISPATCH,
    FEATURE_EQUIPMENT,
    FEATURE_NOTICE,
    FEATURE_TREATMENT,
    FEATURES,
    INTERFACE_VERSION,
    REQUIRED_TOOLS,
    STATUS_TOOL,
    tools_for,
)
from .models import (
    CONTRACT_VERSION,
    ActionKind,
    ActionStatus,
    AlarmStatus,
    DispatchLine,
    DispatchRequest,
    FireAreaStatus,
    NoticeKind,
    NoticeRequest,
    PanelKind,
    PersonnelSnapshot,
    Quality,
    SensorRole,
    StepType,
    TreatmentRequest,
)


class ConformanceError(AssertionError):
    pass


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ConformanceError(msg)


async def check_tools(target: Any) -> None:
    async with connect(target) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    missing = REQUIRED_TOOLS - names
    _check(not missing, f"缺少工具：{sorted(missing)}")
    # 工具集合要和自己声明的功能一致：声明了功能就要有对应工具，没声明的功能不应出现它的工具
    caps = await MineData(target).capabilities()
    unknown = set(caps.features) - set(FEATURES)
    _check(not unknown, f"get_capabilities 声明了接口表里没有的功能：{sorted(unknown)}")
    expected = tools_for(caps.features)
    provided = names & ALL_TOOLS
    _check(provided == expected, f"工具集合与声明的功能不一致：缺 {sorted(expected - provided)}，多 {sorted(provided - expected)}")
    _check(set(caps.tools) == provided, f"get_capabilities 的 tools 与实际工具不一致：{sorted(set(caps.tools) ^ provided)}")


UNKNOWN_SENSOR = "NO-SUCH-SENSOR"


async def check_input_errors(target: Any) -> None:
    """输入错误（如未知测点）必须以工具错误返回、并说明原因。

    MCP SDK 只把 ToolError（mcp.server.mcpserver.exceptions）的说明传给客户端；适配器若抛 ValueError 等，
    Agent 只看到 “Error executing tool …”，不知道错在哪。"""
    results = []
    async with connect(target) as c:  # 在连接内只收集结果：在里面抛错会被包成 ExceptionGroup，说明看不清
        for tool in ("get_latest", "get_readings"):
            r = await c.call_tool(tool, {"sensor_ids": [UNKNOWN_SENSOR]})
            results.append((tool, r.is_error, " ".join(getattr(x, "text", "") for x in r.content)))
    for tool, is_error, text in results:
        _check(is_error, f"{tool} 查询未知测点没有返回错误")
        _check(UNKNOWN_SENSOR in text, f"{tool} 的错误说明没有传给客户端（应抛 ToolError 并写明未知测点）：{text[:80]}")


async def check_capabilities(md: MineData) -> None:
    caps = await md.capabilities()
    _check(caps.interface_version.split(".")[0] == INTERFACE_VERSION.split(".")[0], f"接口主版本不兼容：{caps.interface_version}（本契约 {INTERFACE_VERSION}）")
    _check(caps.contract_version.split(".")[0:2] == CONTRACT_VERSION.split(".")[0:2], f"数据契约版本不一致：{caps.contract_version}（本契约 {CONTRACT_VERSION}）")
    net = await md.network()
    _check(caps.mine_id == net.mine_id, f"get_capabilities 的 mine_id（{caps.mine_id}）与网络（{net.mine_id}）不一致")
    _check(caps.implementation.strip() != "", "implementation 为空")


async def check_description(md: MineData) -> None:
    net = await md.network()
    roadway_ids = {r.id for r in net.roadways}
    node_ids = {n.id for n in net.nodes}
    desc = await md.describe()
    _check(desc.mine_id == net.mine_id, "describe_mine 的 mine_id 与网络不一致")
    panel_ids = [p.panel_id for p in desc.panels]
    _check(len(panel_ids) == len(set(panel_ids)), "工作面编号重复")
    for p in desc.panels:
        for field in ("intake_roadway_id", "return_roadway_id", "face_roadway_id", "goaf_roadway_id"):
            rid = getattr(p, field)
            _check(rid is None or rid in roadway_ids, f"工作面 {p.panel_id} 的 {field}={rid} 不在网络中")
        _check(p.refuge_node_id is None or p.refuge_node_id in node_ids, f"工作面 {p.panel_id} 的避难硐室节点 {p.refuge_node_id} 不在网络中")
        if p.kind == PanelKind.LONGWALL:
            _check(p.goaf_roadway_id is not None, f"综采工作面 {p.panel_id} 没有给出采空区巷道")
    sensors = await md.sensors()
    used = {s.role for s in sensors}
    _check(set(desc.sensor_roles) == used, f"describe_mine 的 sensor_roles 与测点实际角色不一致：{sorted(r.value for r in set(desc.sensor_roles) ^ used)}")
    for s in sensors:
        _check(s.panel is None or s.panel in panel_ids, f"测点 {s.id} 的工作面 {s.panel} 不在 describe_mine 里")
        _check(s.role in SensorRole, f"测点 {s.id} 的角色 {s.role} 不在词表里")


async def check_alarms(md: MineData) -> None:
    caps = await md.capabilities()
    if FEATURE_ALARMS not in caps.features:
        return
    sensor_ids = {s.id for s in await md.sensors()}
    alarms = await md.alarms(hours=72)
    now = await md.now()  # 先取数据、后取时钟：矿井时钟在走时，数据的时间不会晚于之后取到的“现在”
    ids = [a.alarm_id for a in alarms]
    _check(len(ids) == len(set(ids)), "报警编号重复")
    for a in alarms:
        _check(a.raised_at <= now, f"报警 {a.alarm_id} 的发生时间晚于矿井当前时间")
        _check((a.status == AlarmStatus.CLEARED) == (a.cleared_at is not None), f"报警 {a.alarm_id} 的恢复状态与恢复时间不一致")
        _check(a.sensor_id is None or a.sensor_id in sensor_ids, f"报警 {a.alarm_id} 的测点 {a.sensor_id} 不在测点清单中")
        _check(a.acknowledged == (a.acknowledged_at is not None), f"报警 {a.alarm_id} 的确认状态与确认时间不一致")
    active = await md.alarms(hours=72, active_only=True)
    _check(all(a.status == AlarmStatus.ACTIVE for a in active), "active_only 返回了已恢复的报警")


async def check_equipment(md: MineData) -> None:
    caps = await md.capabilities()
    if FEATURE_EQUIPMENT not in caps.features:
        return
    node_ids = {n.id for n in (await md.network()).nodes}
    items = await md.equipment()
    now = await md.now()  # 先取数据、后取时钟（同 check_alarms）
    ids = [e.equipment_id for e in items]
    _check(len(ids) == len(set(ids)), "设备编号重复")
    for e in items:
        _check(e.node_id is None or e.node_id in node_ids, f"设备 {e.equipment_id} 所在节点 {e.node_id} 不在网络中")
        _check(e.updated_at <= now, f"设备 {e.equipment_id} 的更新时间晚于矿井当前时间")


async def check_unknown_action(target: Any) -> None:
    """get_action_status 查未知回执编号要以工具错误返回并写明编号（只读，对真实矿井也安全）。"""
    caps = await MineData(target).capabilities()
    if not set(caps.features) & ACTION_FEATURES:
        return
    async with connect(target) as c:
        r = await c.call_tool(STATUS_TOOL, {"action_id": "NO-SUCH-ACTION"})
        text = " ".join(getattr(x, "text", "") for x in r.content)
    _check(r.is_error, f"{STATUS_TOOL} 查询未知回执编号没有返回错误")
    _check("NO-SUCH-ACTION" in text, f"{STATUS_TOOL} 的错误说明没有写明编号：{text[:80]}")


async def check_actions(md: MineData) -> None:
    """通知、调度、处置：回执、幂等、状态查询、输入错误。会真的执行动作，只在 with_actions 时运行。"""
    caps = await md.capabilities()
    desc = await md.describe()
    panel = desc.panels[0].panel_id
    net = await md.network()
    node = net.nodes[0].id
    key = f"conformance-{uuid.uuid4().hex[:8]}"

    async def twice(kind: ActionKind, send: Any) -> None:
        r1, r2 = await send(key + kind.value), await send(key + kind.value)
        _check(r1.action_id == r2.action_id, f"{kind.value}：相同 idempotency_key 重复提交产生了不同回执")
        _check(r1.kind == kind, f"回执种类应为 {kind.value}，实际 {r1.kind.value}")
        _check(r1.idempotency_key == key + kind.value, "回执没有带回 idempotency_key")
        _check(r1.updated_at >= r1.received_at, "回执的更新时间早于接收时间")
        st = await md.action_status(r1.action_id)
        _check(st.action_id == r1.action_id and st.kind == kind, "get_action_status 返回的不是同一条回执")
        _check(st.status in set(ActionStatus), "回执状态不在枚举里")

    if FEATURE_NOTICE in caps.features:
        await twice(ActionKind.NOTICE, lambda k: md.send_notice(NoticeRequest(kind=NoticeKind.INFO, message="一致性检查（请忽略）", panel=panel, idempotency_key=k)))
        try:
            await md.send_notice(NoticeRequest(kind=NoticeKind.INFO, message="x", panel="NO-SUCH-PANEL", idempotency_key=key + "bad"))
            _check(False, "send_notice 对未知工作面没有报错")
        except MineDataError as exc:
            _check("NO-SUCH-PANEL" in str(exc), f"send_notice 的错误说明没有写明工作面：{exc}")
    if FEATURE_DISPATCH in caps.features:
        line = DispatchLine(item_type="self_rescuer", quantity=1, unit="台", from_node=node, to_node=node)
        await twice(ActionKind.DISPATCH, lambda k: md.dispatch_order(DispatchRequest(purpose="一致性检查（请忽略）", lines=[line], idempotency_key=k)))
    if FEATURE_TREATMENT in caps.features:
        # 只用最轻的动作：监测类步骤不改变矿井状态；真实矿井适配器可以返回 rejected 或 completed
        await twice(ActionKind.TREATMENT, lambda k: md.submit_treatment(TreatmentRequest(panel=panel, type=StepType.MONITOR, idempotency_key=k)))


async def check_network(md: MineData) -> None:
    net = await md.network()
    node_ids = {n.id for n in net.nodes}
    _check(any(n.is_surface for n in net.nodes), "网络中没有地面出口节点")
    for r in net.roadways:
        _check(r.from_node in node_ids and r.to_node in node_ids, f"巷道 {r.id} 引用了不存在的节点")
    ids = [r.id for r in net.roadways]
    _check(len(ids) == len(set(ids)), "巷道 id 重复")


async def check_sensors_and_readings(md: MineData) -> None:
    net = await md.network()
    roadway_ids = {r.id for r in net.roadways}
    sensors = await md.sensors()
    _check(sensors, "没有测点")
    for s in sensors:
        _check(s.roadway_id in roadway_ids, f"测点 {s.id} 所在巷道 {s.roadway_id} 不在网络中")
    now = await md.now()
    latest = await md.latest([s.id for s in sensors])
    _check(latest, "get_latest 没有返回数据")
    for r in latest:
        _check(r.ts <= now, f"{r.sensor_id} 的读数时间晚于矿井当前时间")
        _check((r.value is None) == (r.quality == Quality.MISSING), f"{r.sensor_id} 缺数标记与数值不一致")
    hist = await md.readings([sensors[0].id], start=now - timedelta(hours=6), end=now)
    _check(all(now - timedelta(hours=6) <= r.ts <= now for r in hist), "get_readings 返回了时间窗以外的数据")


async def check_personnel_and_resources(md: MineData) -> None:
    net = await md.network()
    roadway_ids = {r.id for r in net.roadways}
    node_ids = {n.id for n in net.nodes}
    snap = await md.personnel_snapshot()
    for p in snap.people:
        _check(p.roadway_id in roadway_ids, f"人员 {p.person_id} 位置 {p.roadway_id} 不在网络中")
    # 人数必须与名单一致（适配器用 PersonnelSnapshot.of 构造即可）
    _check(snap == PersonnelSnapshot.of(snap.people), "get_personnel 的 total、by_team、by_roadway 与人员名单不一致")
    for item in await md.inventory():
        _check(item.depot_node in node_ids, f"物资 {item.name} 存放节点不在网络中")
        _check(item.quantity >= 0, f"物资 {item.name} 数量为负")
    await md.rescue_teams()


async def check_fire_areas(md: MineData) -> None:
    now = await md.now()
    areas = await md.fire_areas()
    ids = [a.area_id for a in areas]
    _check(len(ids) == len(set(ids)), "火区 id 重复")
    for a in areas:
        _check(a.sealed_at <= now, f"火区 {a.area_id} 的封闭时间晚于矿井当前时间")
        _check((a.status == FireAreaStatus.UNSEALED) == (a.unsealed_at is not None), f"火区 {a.area_id} 启封状态与启封时间不一致")
        if a.unsealed_at is not None:
            _check(a.sealed_at <= a.unsealed_at <= now, f"火区 {a.area_id} 的启封时间不合理")


CHECKS: list[Callable[[MineData], Awaitable[None]]] = [
    check_capabilities,
    check_description,
    check_alarms,
    check_equipment,
    check_network,
    check_sensors_and_readings,
    check_personnel_and_resources,
    check_fire_areas,
]


async def run_all(target: Any, with_actions: bool = False) -> list[tuple[str, str | None]]:
    """返回 [(检查名, 错误信息或 None)]。with_actions 为真才运行会真的执行动作的检查（对真实矿井不要打开）。"""
    results: list[tuple[str, str | None]] = []
    try:
        await check_tools(target)
        results.append(("check_tools", None))
    except Exception as exc:  # noqa: BLE001
        results.append(("check_tools", str(exc)))
        return results
    try:
        await check_input_errors(target)
        results.append(("check_input_errors", None))
    except Exception as exc:  # noqa: BLE001
        results.append(("check_input_errors", f"{type(exc).__name__}: {exc}"))
    try:
        await check_unknown_action(target)
        results.append(("check_unknown_action", None))
    except Exception as exc:  # noqa: BLE001
        results.append(("check_unknown_action", f"{type(exc).__name__}: {exc}"))
    md = MineData(target)
    for check in [*CHECKS, *([check_actions] if with_actions else [])]:
        try:
            await check(md)
            results.append((check.__name__, None))
        except Exception as exc:  # noqa: BLE001
            results.append((check.__name__, f"{type(exc).__name__}: {exc}"))
    return results


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    url = args[0] if args else "http://127.0.0.1:8101/mcp"
    results = asyncio.run(run_all(url, with_actions="--with-actions" in sys.argv))
    failed = 0
    for name, err in results:
        print(f"{'PASS' if err is None else 'FAIL'}  {name}{'' if err is None else '  ' + err}")
        failed += err is not None
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
