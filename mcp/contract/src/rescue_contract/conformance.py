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
from .models import Quality

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
    for p in await md.personnel():
        _check(p.roadway_id in roadway_ids, f"人员 {p.person_id} 位置 {p.roadway_id} 不在网络中")
    for item in await md.inventory():
        _check(item.depot_node in node_ids, f"物资 {item.name} 存放节点不在网络中")
        _check(item.quantity >= 0, f"物资 {item.name} 数量为负")
    await md.rescue_teams()


CHECKS: list[Callable[[MineData], Awaitable[None]]] = [
    check_network,
    check_sensors_and_readings,
    check_personnel_and_resources,
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
