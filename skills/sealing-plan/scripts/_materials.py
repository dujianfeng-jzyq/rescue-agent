"""物资用量估算。由 mcp/dispatch 的 server.py（seal_materials、grout_volume、estimate_inerting、supply）迁移，算法不变；
参数在 references/materials.yaml（开发占位值）。"""

from __future__ import annotations

from typing import Any


def seal_materials(walls: int, kind: str, cfg: dict[str, Any], section_m2: float | None = None) -> dict[str, Any]:
    """密闭材料用量。永久密闭：墙体体积 = 断面 × 墙厚，砖、砂浆、水泥、砂按单位用量换算；临时密闭：按套数。"""
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


def grout_volume(strike_m: float, face_m: float, c: dict[str, Any]) -> dict[str, Any]:
    """注浆量 = 采空区走向长 × 工作面长 × 采高 × 注浆系数；干料体积 = 浆液 ÷ (水土比 + 1)；质量 = 干料体积 × 堆积密度。"""
    goaf_m3 = strike_m * face_m * c["mining_height_m"]
    slurry = goaf_m3 * c["coefficient"]
    dry_m3 = slurry / (c["water_soil_ratio"] + 1)
    return {
        "goaf_volume_m3": round(goaf_m3, 3),
        "slurry_m3": round(slurry, 3),
        "dry_material_m3": round(dry_m3, 3),
        "dry_material_t": round(dry_m3 * c["bulk_density_t_m3"], 3),
    }


def estimate_inerting(leak_m3_min: float, current_o2_pct: float, target_o2_pct: float, penetration: float) -> dict[str, Any]:
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


def supply(stock: list[dict[str, Any]], item_type: str, required: float) -> dict[str, Any]:
    """台账现有量与缺口。台账没有这类物资时现有量为 0，in_ledger=false。"""
    items = [s for s in stock if s["item_type"] == item_type]
    on_hand = sum(s["quantity"] for s in items)
    return {
        "item_type": item_type,
        "name": items[0]["name"] if items else None,
        "required": round(required, 3),
        "on_hand": round(on_hand, 3),
        "unit": items[0]["unit"] if items else None,
        "shortage": round(max(0.0, required - on_hand), 3),
        "sufficient": on_hand >= required,
        "in_ledger": bool(items),
    }
