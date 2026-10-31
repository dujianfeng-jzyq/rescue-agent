import asyncio

import pytest
from mcp.client import Client
from rescue_dispatch.server import build_server, estimate_inerting
from rescue_mine_data_sim.server import SimHolder
from rescue_mine_data_sim.server import build_server as build_mine


def test_estimate_inerting_matches_mixing_model():
    r = estimate_inerting(leak_m3_min=40, current_o2_pct=9.0, target_o2_pct=6.0)
    # 2400 m³/h 漏风，目标降到 2/3，需要 η·Q = 1200 → Q ≈ 1333
    assert r["required_n2_m3_h"] == pytest.approx(1333, abs=2)


def test_estimate_inerting_rejects_bad_target():
    with pytest.raises(ValueError):
        estimate_inerting(40, 9.0, 12.0)


def _call(tool, args):
    server = build_server(build_mine(SimHolder("s2-slow-oxidation", manual_clock=True)))

    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args)

    return asyncio.run(go())


def test_draft_dispatch_order_picks_depot_and_eta():
    r = _call(
        "draft_dispatch_order",
        {"items": [{"item_type": "temporary_seal_kit", "quantity": 2}], "destination_node": "N4", "purpose": "上隅角临时密闭"},
    )
    assert not r.is_error, r.content
    line = r.structured_content["lines"][0]
    assert line["from_node"] == "N2" and line["eta_min"] > 0


def test_draft_dispatch_order_insufficient_stock():
    r = _call(
        "draft_dispatch_order", {"items": [{"item_type": "temporary_seal_kit", "quantity": 99}], "destination_node": "N4", "purpose": "x"}
    )
    assert r.is_error


def test_inert_gas_reports_capacity_shortage():
    r = _call("estimate_inert_gas", {"leak_m3_min": 40, "current_o2_pct": 9.0, "target_o2_pct": 3.0})
    sup = r.structured_content["supply"]
    # 需求 = 2400 × (9/3 − 1) / 0.9 ≈ 5333 m³/h，台账制氮产能 1200 m³/h
    assert sup["on_hand"] == 1200 and sup["shortage"] == sup["required"] - 1200 and not sup["sufficient"]


def test_check_supply_flags_shortage_and_missing_items():
    r = _call("check_supply", {"items": [{"item_type": "grout_material_t", "quantity": 500}, {"item_type": "self_rescuer", "quantity": 22}, {"item_type": "brick_pcs", "quantity": 100}]})
    rows = {x["item_type"]: x for x in r.structured_content["items"]}
    assert rows["grout_material_t"]["shortage"] == 200 and not rows["grout_material_t"]["sufficient"]
    assert rows["self_rescuer"]["sufficient"] and rows["self_rescuer"]["shortage"] == 0
    assert rows["brick_pcs"]["in_ledger"] is False and rows["brick_pcs"]["on_hand"] == 0
    assert r.structured_content["short_items"] == ["grout_material_t", "brick_pcs"]


def test_route_sums_segments():
    r = _call("route", {"from_node": "N1", "to_node": "N4"}).structured_content
    assert r["nodes"][0] == "N1" and r["nodes"][-1] == "N4"
    assert r["length_m"] == sum(s["length_m"] for s in r["segments"])
    assert "R-1201-GOAF" not in {s["roadway_id"] for s in r["segments"]}


def test_seal_materials_permanent_and_temporary():
    from rescue_dispatch.server import seal_materials

    r = seal_materials(2, "permanent", 10.0)
    assert r["total_volume_m3"] == pytest.approx(2 * 10.0 * 0.8)
    assert r["materials"]["brick_pcs"] == round(16 * 512)
    assert seal_materials(3, "temporary")["materials"] == {"temporary_seal_kit": 3}
    with pytest.raises(ValueError):
        seal_materials(1, "wooden")


def test_seal_materials_tool_compares_kits_with_ledger():
    r = _call("estimate_seal_materials", {"walls": 8, "kind": "temporary"}).structured_content
    kit = r["supply"][0]
    assert kit["item_type"] == "temporary_seal_kit" and kit["on_hand"] == 6 and kit["shortage"] == 2


def test_grout_from_network_dimensions():
    r = _call("estimate_grout", {"panel": "1201"}).structured_content
    # 250 m（采空区漏风通道）× 220 m（工作面）× 3.0 m × 0.05 = 8250 m³ 浆液，÷ 6 × 1.5 = 2062.5 t 干料
    assert r["slurry_m3"] == pytest.approx(8250) and r["dry_material_t"] == pytest.approx(2062.5)
    assert r["supply"]["on_hand"] == 300 and r["supply"]["shortage"] == pytest.approx(1762.5)
    assert "漏风通道" in r["assumptions"]["strike_from"]
