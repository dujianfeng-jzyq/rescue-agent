"""workflow 测试的公用函数（conftest 把本目录加入 sys.path）。"""

import asyncio

from mcp.client import Client
from rescue_mine_data_sim.server import SimHolder
from rescue_mine_data_sim.server import build_server as build_mine


def sim(scenario: str, hours: float) -> SimHolder:
    holder = SimHolder(scenario, manual_clock=True)
    holder.engine.clock.advance(hours)
    return holder


def call(server, tool: str, args: dict | None = None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args or {})

    return asyncio.run(go())


__all__ = ["build_mine", "call", "sim"]
