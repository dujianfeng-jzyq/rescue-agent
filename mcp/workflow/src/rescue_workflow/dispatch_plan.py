"""W7 调度单草案：从已核对的方案算物资用量、比较库存、生成调度单草案（docs/workflow-design.md W7）。

密闭道数由方案里密闭步骤填写的巷道数决定（由代码数，不让智能体数）；参数为 dispatch 的 params.yaml 占位值。
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from rescue_contract.client import MineData
from rescue_contract.models import AirwayKind, Plan, StepType
from rescue_dispatch.server import ItemRequest, grout_volume, plan_order, seal_materials, supply


async def default_destination(md: MineData, panel: str) -> str:
    """缺省目的地：本工作面进风巷靠工作面一端的节点（材料先运到这里）。"""
    for r in (await md.network()).roadways:
        if r.kind == AirwayKind.INTAKE and panel in r.id:
            return r.to_node
    raise ToolError(f"网络中找不到工作面 {panel} 的进风巷，请给出 destination_node")


async def draft(md: MineData, plan: Plan, panel: str, destination_node: str | None) -> dict[str, Any]:
    dest = destination_node or await default_destination(md, panel)
    stock = await md.inventory()
    needs: dict[str, float] = {}
    notes: list[str] = []
    calcs: dict[str, Any] = {}
    for kind, step_type in (("temporary", StepType.TEMPORARY_SEAL), ("permanent", StepType.PERMANENT_SEAL)):
        steps = [s for s in plan.steps if s.type == step_type]
        if not steps:
            continue
        walls = sum(len(s.roadways) for s in steps)
        if walls == 0:
            notes.append(f"{'临时' if kind == 'temporary' else '永久'}密闭步骤没有填写巷道（密闭位置），不能计算材料；填写后重新生成")
            continue
        r = seal_materials(walls, kind)
        calcs[f"{kind}_seal"] = r
        for item, qty in r["materials"].items():
            if item != "mortar_m3":  # 砂浆现场拌制，按水泥、砂调运
                needs[item] = needs.get(item, 0) + qty
    if any(s.type == StepType.GROUTING for s in plan.steps):
        net = await md.network()
        face = next((r for r in net.roadways if r.kind == AirwayKind.WORKING_FACE and panel in r.id), None)
        goaf = next((r for r in net.roadways if r.kind == AirwayKind.GOAF and panel in r.id), None)
        if face is None or goaf is None:
            notes.append(f"网络中找不到工作面 {panel} 的工作面巷道或采空区，不能估算注浆量")
        else:
            g = grout_volume(goaf.length_m, face.length_m)
            calcs["grout"] = {**g, "strike_from": f"采空区漏风通道 {goaf.id} 长度（数据中没有采空区走向长度）", "face_from": face.id}
            needs["grout_material_t"] = needs.get("grout_material_t", 0) + g["dry_material_t"]
    inert = [s.params.get("rate_m3_h") for s in plan.steps if s.type == StepType.INERT_INJECTION and "rate_m3_h" in s.params]
    capacity = supply(stock, "nitrogen_capacity_m3_h", float(max(inert))) if inert else None
    if not inert and any(s.type == StepType.INERT_INJECTION for s in plan.steps):
        notes.append("注惰步骤没有注氮流量，不能比较制氮产能")
    rows = [supply(stock, item, qty) for item, qty in needs.items()]
    ok_items = [ItemRequest(item_type=r["item_type"], quantity=r["required"]) for r in rows if r["sufficient"] and r["required"] > 0]
    order = None
    if ok_items:
        try:
            order = plan_order(await md.network(), stock, ok_items, dest, f"{plan.plan_id} {plan.title}").model_dump(mode="json")
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
    return {
        "panel": panel,
        "plan_id": plan.plan_id,
        "plan_snapshot_id": plan.snapshot_id,
        "destination_node": dest,
        "destination_from": "调用方给出" if destination_node else f"缺省：工作面 {panel} 进风巷靠工作面一端",
        "calculations": calcs,
        "supply": rows,
        "shortages": [r for r in rows if not r["sufficient"]],
        "nitrogen_capacity": capacity,
        "order": order,
        "notes": notes,
        "method": "密闭道数取方案密闭步骤的巷道数；用量按 dispatch 的 params.yaml 换算（开发占位值）；存放点按距离贪心选择",
    }
