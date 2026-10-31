"""dispatch 服务：防灭火用量估算、库存缺口和物资调度草案。

用量、缺口、路程都在这里算好，Agent 照抄，不要自己相加相减或套公式。估算参数见 params.yaml（开发占位值）。
这里只生成“草案”。调度单要经 actions 服务的 issue_dispatch_order 发布，并经过人工确认。
TODO(算法): 多物资、多目的地时用整数规划求解；现在是按距离贪心选库存点。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import networkx as nx
import yaml
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field
from rescue_contract.client import MineData
from rescue_contract.models import AirwayKind, DispatchLine, DispatchOrder, MineNetwork, ResourceItem

INSTRUCTIONS = (
    "物资调度服务。注氮量等用量由公式估算，调度路线和到达时间由网络计算，引用时照抄数值。"
    "缺口用 check_supply（或估算工具返回的 supply），路程用 route，不要自己相加相减。"
    "draft_dispatch_order 只生成草案，发布须调用 actions 服务并经人工确认。"
)
PARAMS = yaml.safe_load(Path(__file__).with_name("params.yaml").read_text(encoding="utf-8"))
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


def supply(stock: list[ResourceItem], item_type: str, required: float) -> dict[str, Any]:
    """台账现有量与缺口。台账没有这类物资时现有量为 0，in_ledger=false。"""
    items = [s for s in stock if s.item_type == item_type]
    on_hand = sum(s.quantity for s in items)
    return {
        "item_type": item_type,
        "required": round(required, 3),
        "on_hand": round(on_hand, 3),
        "unit": items[0].unit if items else None,
        "shortage": round(max(0.0, required - on_hand), 3),
        "sufficient": on_hand >= required,
        "in_ledger": bool(items),
        "depots": [{"node": s.depot_node, "quantity": s.quantity} for s in items],
    }


def seal_materials(walls: int, kind: str, section_m2: float | None = None) -> dict[str, Any]:
    """密闭材料用量。永久密闭：墙体体积 = 断面 × 墙厚，砖、砂浆、水泥、砂按单位用量换算；临时密闭：按套数。"""
    cfg = PARAMS["seal"]
    if walls < 1:
        raise ValueError("密闭道数至少为 1")
    if kind == "temporary":
        n = walls * cfg["temporary"]["kits_per_wall"]
        return {"kind": kind, "walls": walls, "materials": {"temporary_seal_kit": n}, "assumptions": {"kits_per_wall": cfg["temporary"]["kits_per_wall"]}}
    if kind != "permanent":
        raise ValueError("kind 取 temporary（临时密闭）或 permanent（永久密闭）")
    c = cfg["permanent"]
    area = section_m2 if section_m2 is not None else cfg["default_section_m2"]
    wall_m3 = area * c["wall_thickness_m"]
    total_m3 = wall_m3 * walls
    mortar = total_m3 * c["mortar_m3_per_m3"]
    return {
        "kind": kind,
        "walls": walls,
        "wall_volume_m3": round(wall_m3, 3),
        "total_volume_m3": round(total_m3, 3),
        "materials": {
            "brick_pcs": round(total_m3 * c["bricks_per_m3"]),
            "mortar_m3": round(mortar, 3),
            "cement_t": round(mortar * c["cement_kg_per_m3_mortar"] / 1000, 3),
            "sand_m3": round(mortar * c["sand_m3_per_m3_mortar"], 3),
        },
        "assumptions": {"section_m2": area, "section_from": "参数" if section_m2 is not None else "缺省断面（网络无断面数据）", **c},
    }


def grout_volume(strike_m: float, face_m: float, cfg: dict | None = None) -> dict[str, Any]:
    """注浆量 = 采空区走向长 × 工作面长 × 采高 × 注浆系数；干料体积 = 浆液 ÷ (水土比 + 1)；质量 = 干料体积 × 堆积密度。"""
    c = cfg or PARAMS["grout"]
    goaf_m3 = strike_m * face_m * c["mining_height_m"]
    slurry = goaf_m3 * c["coefficient"]
    dry_m3 = slurry / (c["water_soil_ratio"] + 1)
    return {
        "goaf_volume_m3": round(goaf_m3, 3),
        "slurry_m3": round(slurry, 3),
        "dry_material_m3": round(dry_m3, 3),
        "dry_material_t": round(dry_m3 * c["bulk_density_t_m3"], 3),
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
        """估算把高温区氧浓度从 current 降到 target 所需的注氮流量（m³/h），并与台账制氮产能比较：
        产能缺口照抄 supply.shortage，不要自己相减。"""
        try:
            r = estimate_inerting(leak_m3_min, current_o2_pct, target_o2_pct)
        except ValueError as exc:  # 只有 ToolError 的说明能传给 Agent，其他异常只显示“执行出错”
            raise ToolError(str(exc)) from exc
        return {**r, "supply": supply(await md.inventory(), "nitrogen_capacity_m3_h", r["required_n2_m3_h"])}

    @server.tool()
    async def check_supply(items: list[ItemRequest]) -> dict[str, Any]:
        """逐项比较需求量与台账现有量：on_hand、shortage（缺口）、sufficient（是否够）。
        缺口和是否够用照抄这里，不要自己用需求减库存。台账没有的物资 in_ledger=false。"""
        stock = await md.inventory()
        rows = [supply(stock, i.item_type, i.quantity) for i in items]
        return {"items": rows, "all_sufficient": all(r["sufficient"] for r in rows), "short_items": [r["item_type"] for r in rows if not r["sufficient"]]}

    @server.tool()
    async def route(from_node: str, to_node: str) -> dict[str, Any]:
        """两节点间最短运输路线（不经采空区）：经过的巷道、各段长度、总长 length_m、预计到达时间 eta_min。
        路程照抄 length_m，不要自己把各段相加。"""
        net = await md.network()
        g = _graph(net)
        for n in (from_node, to_node):
            if n not in g:
                raise ToolError(f"节点不存在或不可达：{n}")
        nodes = nx.shortest_path(g, from_node, to_node, weight="length")
        by_ends = {frozenset((r.from_node, r.to_node)): r for r in net.roadways if r.kind != AirwayKind.GOAF}
        segments = [by_ends[frozenset(pair)] for pair in zip(nodes, nodes[1:], strict=False)]
        total = sum(r.length_m for r in segments)
        return {
            "nodes": nodes,
            "segments": [{"roadway_id": r.id, "name": r.name, "length_m": r.length_m} for r in segments],
            "length_m": round(total, 1),
            "eta_min": round(total / TRANSPORT_SPEED_M_S / 60, 1),
            "assumptions": {"transport_speed_m_s": TRANSPORT_SPEED_M_S},
        }

    @server.tool()
    async def estimate_seal_materials(walls: int, kind: str = "permanent", section_m2: float | None = None) -> dict[str, Any]:
        """估算密闭材料用量并与台账比较。kind：permanent（永久密闭，砖、砂浆、水泥、砂）或 temporary（临时密闭，快速密闭材料套数）。
        section_m2 只在图纸或现场给出断面时填写，否则用缺省断面（结果里注明）。用量和缺口照抄 materials、supply。
        参数为开发占位值（params.yaml），需按本矿密闭设计确认。"""
        try:
            r = seal_materials(walls, kind, section_m2)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        stock = await md.inventory()
        rows = [supply(stock, k, v) for k, v in r["materials"].items() if k != "mortar_m3"]
        return {**r, "supply": rows, "method": "按单位用量换算，参数为开发占位值（params.yaml），需按本矿密闭设计确认"}

    @server.tool()
    async def estimate_grout(panel: str = "1201", strike_length_m: float | None = None) -> dict[str, Any]:
        """估算采空区注浆量和干料用量，并与台账注浆材料比较（缺口照抄 supply.shortage）。
        工作面长度取网络中该工作面巷道长度；采空区走向长度只在指挥人员给出时填 strike_length_m，
        否则取采空区漏风通道长度（结果里注明）。参数为开发占位值（params.yaml）。"""
        net = await md.network()
        face = next((r for r in net.roadways if r.kind == AirwayKind.WORKING_FACE and panel in r.id), None)
        goaf = next((r for r in net.roadways if r.kind == AirwayKind.GOAF and panel in r.id), None)
        if face is None or goaf is None:
            raise ToolError(f"网络中找不到工作面 {panel} 的工作面巷道或采空区")
        strike = strike_length_m if strike_length_m is not None else goaf.length_m
        r = grout_volume(strike, face.length_m)
        return {
            "panel": panel,
            **r,
            "supply": supply(await md.inventory(), "grout_material_t", r["dry_material_t"]),
            "assumptions": {
                "strike_length_m": strike,
                "strike_from": "指挥人员给出" if strike_length_m is not None else f"采空区漏风通道 {goaf.id} 长度（数据中没有采空区走向长度）",
                "face_length_m": face.length_m,
                "face_from": face.id,
                **PARAMS["grout"],
            },
            "method": "注浆系数法，参数为开发占位值（params.yaml），需按本矿注浆工艺确认",
        }

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
