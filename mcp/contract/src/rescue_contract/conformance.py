"""mine-data 一致性检查：模拟矿井和真实矿井适配器跑同一套检查，全部通过才能替换上线。

用法：
    python -m rescue_contract.conformance http://127.0.0.1:8101/mcp
测试里也可以传入进程内的 MCPServer 实例。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from .client import MineData, connect
from .models import FireAreaStatus, PersonnelSnapshot, Quality

REQUIRED_TOOLS = {
    "get_clock",
    "get_network",
    "list_sensors",
    "get_readings",
    "get_latest",
    "get_events",
    "get_personnel",
    "get_inventory",
    "get_rescue_teams",
    "get_fire_areas",
}


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
    check_network,
    check_sensors_and_readings,
    check_personnel_and_resources,
    check_fire_areas,
]


async def run_all(target: Any) -> list[tuple[str, str | None]]:
    """返回 [(检查名, 错误信息或 None)]。"""
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
    md = MineData(target)
    for check in CHECKS:
        try:
            await check(md)
            results.append((check.__name__, None))
        except Exception as exc:  # noqa: BLE001
            results.append((check.__name__, f"{type(exc).__name__}: {exc}"))
    return results


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8101/mcp"
    results = asyncio.run(run_all(url))
    failed = 0
    for name, err in results:
        print(f"{'PASS' if err is None else 'FAIL'}  {name}{'' if err is None else '  ' + err}")
        failed += err is not None
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
