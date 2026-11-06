"""跨服务检查：工具因输入错误失败时，Agent 必须看到原因。

MCP SDK 只把 ToolError 的说明传给客户端；其他异常（如 ValueError）只显示“Error executing tool …”，
Agent 不知道错在哪、也无法改正（如库存不足时不知道现有多少）。
"""

import asyncio

import pytest
from mcp.client import Client
from rescue_mine_data_sim.server import SimHolder
from rescue_mine_data_sim.server import build_server as mine_data


def _mine():
    return mine_data(SimHolder("s2-slow-oxidation", manual_clock=True))


CASES = [
    (lambda: _mine(), "get_latest", {"sensor_ids": ["NO-SUCH"]}, "未知测点"),
    (lambda: _mine(), "get_readings", {"sensor_ids": ["NO-SUCH"]}, "未知测点"),
]


@pytest.mark.parametrize(("build", "tool", "args", "expect"), CASES, ids=[f"{c[1]}-{c[3]}" for c in CASES])
def test_input_errors_reach_the_agent(build, tool, args, expect, tmp_path, monkeypatch):
    monkeypatch.setenv("RESCUE_CHART_DIR", str(tmp_path))  # 爆炸三角形工具成功时会存图
    monkeypatch.setenv("RESCUE_ACTIONS_LEDGER", str(tmp_path / "ledger.jsonl"))
    async def go():
        async with Client(build()) as c:
            return await c.call_tool(tool, args)

    r = asyncio.run(go())
    assert r.is_error
    assert expect in r.content[0].text, r.content[0].text
