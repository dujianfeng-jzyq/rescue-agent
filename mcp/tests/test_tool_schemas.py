"""跨服务检查：所有工具的输入、输出 schema 都不能含 format: date-time（DSH 严格校验，见 MineTime）。"""

import asyncio
import json

import pytest
from mcp.client import Client
from rescue_actions.server import build_server as actions
from rescue_mine_data_sim.server import build_server as mine_data


@pytest.mark.parametrize("build", [mine_data, actions], ids=lambda b: b.__module__.split(".")[0])
def test_no_date_time_format_in_tool_schemas(build):
    async def go():
        async with Client(build()) as c:
            return (await c.list_tools()).tools

    bad = [t.name for t in asyncio.run(go()) if "date-time" in json.dumps([t.input_schema, t.output_schema])]
    assert bad == []
