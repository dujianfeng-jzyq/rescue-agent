"""dispatch 服务：防灭火用量估算和物资调度草案。

这里只生成“草案”。调度单要经 actions 服务的 issue_dispatch_order 发布，并经过人工确认。
TODO(算法): 多物资、多目的地时用整数规划求解；现在是按距离贪心选库存点。
"""

from __future__ import annotations

import uuid
from typing import Any

import networkx as nx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field
from rescue_contract.client import MineData
from rescue_contract.models import DispatchLine, DispatchOrder, MineNetwork

INSTRUCTIONS = (
    "物资调度服务。注氮量等用量由公式估算，调度路线和到达时间由网络计算，引用时照抄数值。"
    "draft_dispatch_order 只生成草案，发布须调用 actions 服务并经人工确认。"
)
TRANSPORT_SPEED_M_S = 1.0  # 井下运输平均速度。TODO(数据): 按本矿运输方式（无轨胶轮车、人力）设定


class ItemRequest(BaseModel):
    item_type: str = Field(description="物资类型，取值见 list_resources 返回的 item_type")
    quantity: float = Field(gt=0)


def _graph(net: MineNetwork) -> nx.Graph:
    g = nx.Graph()
    for r in net.roadways:
        if r.kind.value != "goaf":
            g.add_edge(r.from_node, r.to_node, length=r.length_m)
    return g


def estimate_inerting(leak_m3_min: float, current_o2_pct: float, target_o2_pct: float, penetration: float = 0.9) -> dict[str, Any]:
    """按漏风与氮气混合估算所需注氮流量：O₂_target = O₂_current × Q_leak / (Q_leak + η·Q_N₂)。"""
    if not 0 < target_o2_pct < current_o2_pct:
        raise ValueError("目标氧浓度应低于当前氧浓度且大于 0")
    q_leak = leak_m3_min * 60
    q_n2 = q_leak * (current_o2_pct / target_o2_pct - 1) / penetration
    return {
        "required_n2_m3_h": round(q_n2),
        "assumptions": {"leak_m3_h": q_leak, "penetration": penetration},
        "method": "稀释混合估算，未考虑采空区氧化耗氧和漏风分布，需专家确认",
    }


def build_server(mine_data_target: Any | None = None) -> MCPServer:
    md = MineData(mine_data_target) if mine_data_target is not None else MineData.from_env()
    server = MCPServer("dispatch", instructions=INSTRUCTIONS)

    @server.tool()
    async def list_resources() -> dict[str, Any]:
        """应急物资和救援力量清单。"""
        return {
            "inventory": [i.model_dump() for i in await md.inventory()],
            "rescue_teams": [t.model_dump() for t in await md.rescue_teams()],
        }

    @server.tool()
    async def estimate_inert_gas(leak_m3_min: float, current_o2_pct: float, target_o2_pct: float) -> dict[str, Any]:
        """估算把高温区氧浓度从 current 降到 target 所需的注氮流量（m³/h）。"""
        try:
            return estimate_inerting(leak_m3_min, current_o2_pct, target_o2_pct)
        except ValueError as exc:  # 只有 ToolError 的说明能传给 Agent，其他异常只显示“执行出错”
            raise ToolError(str(exc)) from exc

    @server.tool()
    async def draft_dispatch_order(items: list[ItemRequest], destination_node: str, purpose: str) -> DispatchOrder:
        """生成调度单草案：为每项物资选择库存充足且路程最短的存放点，给出路线距离和预计到达时间。"""
        net = await md.network()
        g = _graph(net)
        if destination_node not in g:
            raise ToolError(f"目的地节点不存在或不可达：{destination_node}")
        stock = await md.inventory()
        lines = []
        for req in items:
            candidates = [s for s in stock if s.item_type == req.item_type and s.quantity >= req.quantity]
            if not candidates:
                have = sum(s.quantity for s in stock if s.item_type == req.item_type)
                raise ToolError(f"{req.item_type} 库存不足：需要 {req.quantity}，现有 {have}")
            best = min(candidates, key=lambda s: nx.shortest_path_length(g, s.depot_node, destination_node, weight="length"))
            dist = nx.shortest_path_length(g, best.depot_node, destination_node, weight="length")
            lines.append(
                DispatchLine(
                    item_type=req.item_type,
                    quantity=req.quantity,
                    unit=best.unit,
                    from_node=best.depot_node,
                    to_node=destination_node,
                    eta_min=round(dist / TRANSPORT_SPEED_M_S / 60, 1),
                )
            )
        return DispatchOrder(order_id=f"DO-{uuid.uuid4().hex[:8]}", purpose=purpose, lines=lines)

    return server
