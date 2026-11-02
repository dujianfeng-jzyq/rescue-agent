"""跨服务检查：工具因输入错误失败时，Agent 必须看到原因。

MCP SDK 只把 ToolError 的说明传给客户端；其他异常（如 ValueError）只显示“Error executing tool …”，
Agent 不知道错在哪、也无法改正（如库存不足时不知道现有多少）。
"""

import asyncio

import pytest
from mcp.client import Client
from rescue_dispatch.server import build_server as dispatch
from rescue_fire_model.server import build_server as fire_model
from rescue_kb.server import KnowledgeBase
from rescue_kb.server import build_server as kb
from rescue_mine_data_sim.server import SimHolder
from rescue_mine_data_sim.server import build_server as mine_data
from rescue_ventnet.server import build_server as ventnet


def _mine():
    return mine_data(SimHolder("s2-slow-oxidation", manual_clock=True))


CASES = [
    (lambda: _mine(), "get_latest", {"sensor_ids": ["NO-SUCH"]}, "未知测点"),
    (lambda: _mine(), "get_readings", {"sensor_ids": ["NO-SUCH"]}, "未知测点"),
    (lambda: dispatch(_mine()), "estimate_inert_gas", {"leak_m3_min": 40, "current_o2_pct": 5, "target_o2_pct": 8}, "目标氧浓度"),
    (lambda: dispatch(_mine()), "draft_dispatch_order", {"items": [{"item_type": "grout_material_t", "quantity": 99999}], "destination_node": "N1", "purpose": "测试"}, "库存不足"),
    (lambda: dispatch(_mine()), "draft_dispatch_order", {"items": [{"item_type": "grout_material_t", "quantity": 1}], "destination_node": "NO-SUCH", "purpose": "测试"}, "目的地节点"),
    (lambda: ventnet(_mine()), "downstream_roadways", {"source_roadway_id": "NO-SUCH"}, "未知巷道"),
    (lambda: ventnet(_mine()), "threatened_personnel", {"source_roadway_id": "NO-SUCH"}, "未知巷道"),
    (lambda: fire_model(_mine()), "assess_explosion_risk", {"sensor_id": "NO-SUCH"}, "未知测点"),
    (lambda: fire_model(_mine()), "assess_explosion_risk", {"sensor_id": "SM-1201-UC"}, "不测 CO2"),
    (lambda: fire_model(_mine()), "assess_explosion_risk_sample", {"ch4_pct": 80, "o2_pct": 30, "co2_pct": 1, "temperature_c": 25}, "超过 100%"),
    (lambda: fire_model(_mine()), "assess_explosion_risk_sample", {"ch4_pct": 3, "o2_pct": 10, "co2_pct": 1, "temperature_c": 300}, "适用范围"),
    (lambda: kb(KnowledgeBase(cfg=None)), "get_article", {"document": "煤矿安全规程", "article": "附录A"}, "认不出条号"),
    (lambda: fire_model(_mine()), "evaluate_effect", {"sensor_id": "BT-1201-G1", "since": "2026-11-04T08:00:00", "measurand": "CH4"}, "不是发火指标"),
]


@pytest.mark.parametrize(("build", "tool", "args", "expect"), CASES, ids=[f"{c[1]}-{c[3]}" for c in CASES])
def test_input_errors_reach_the_agent(build, tool, args, expect, tmp_path, monkeypatch):
    monkeypatch.setenv("RESCUE_CHART_DIR", str(tmp_path))  # 爆炸三角形工具成功时会存图
    async def go():
        async with Client(build()) as c:
            return await c.call_tool(tool, args)

    r = asyncio.run(go())
    assert r.is_error
    assert expect in r.content[0].text, r.content[0].text
