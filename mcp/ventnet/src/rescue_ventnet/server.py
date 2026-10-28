"""ventnet 服务：通风网络分析。数据从 mine-data 服务取（经数据契约客户端），不经过大模型。"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from rescue_contract.client import MineData

from . import network

INSTRUCTIONS = (
    "通风网络分析服务。给出有害气体沿风流蔓延的范围和到达时间、受威胁人员及避灾路线。"
    "结果基于网络拓扑和设计风量估算，尚未做风量解算，在结论中要说明这一点。"
)


def build_server(mine_data_target: Any | None = None) -> MCPServer:
    md = MineData(mine_data_target) if mine_data_target is not None else MineData.from_env()
    server = MCPServer("ventnet", instructions=INSTRUCTIONS)

    @server.tool()
    async def downstream_roadways(source_roadway_id: str) -> list[dict[str, Any]]:
        """源头巷道下风侧会受有害气体影响的巷道，按估算到达时间（分钟）排序。"""
        net = await md.network()
        return [{"roadway_id": e.roadway_id, "arrival_min": e.arrival_min} for e in network.downstream(net, source_roadway_id)]

    @server.tool()
    async def threatened_personnel(source_roadway_id: str) -> dict[str, Any]:
        """结合人员定位，列出下风侧受威胁人员、有害气体到达时间和每组人员的避灾路线。
        这是补充撤人名单的依据；撤人本身由调度员按预案立即执行，不等待本结果。"""
        net = await md.network()
        people = await md.personnel()
        return network.threatened(net, source_roadway_id, people)

    @server.tool()
    async def escape_route(from_roadway_id: str, avoid_roadway_ids: list[str] | None = None) -> dict[str, Any]:
        """从指定巷道到地面出口或避难硐室的最短路线，可指定需避开的巷道。"""
        net = await md.network()
        return network.escape_route(net, from_roadway_id, set(avoid_roadway_ids or []))

    return server
