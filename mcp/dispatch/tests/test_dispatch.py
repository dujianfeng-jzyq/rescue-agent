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
