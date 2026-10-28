"""serving.ensure_text_content：空列表结果也要有模型可见的 content。"""

import asyncio
import json

from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from rescue_contract.serving import ensure_text_content


def _server() -> MCPServer:
    server = MCPServer("t")

    @server.tool()
    def items(n: int = 0) -> list[int]:
        return list(range(n))

    return server


def _call(server: MCPServer, n: int):
    async def go():
        async with Client(server) as c:
            return await c.call_tool("items", {"n": n})

    return asyncio.run(go())


def test_sdk_still_returns_empty_content_for_empty_list():
    # 记录 SDK 现状：这条失败说明 SDK 行为变了，ensure_text_content 可能可以去掉
    r = _call(_server(), 0)
    assert r.content == [] and r.structured_content == {"result": []}


def test_empty_list_gets_json_text():
    r = _call(ensure_text_content(_server()), 0)
    assert json.loads(r.content[0].text) == {"result": []}
    assert r.structured_content == {"result": []}


def test_non_empty_result_unchanged_and_idempotent():
    server = ensure_text_content(ensure_text_content(_server()))
    r = _call(server, 2)
    assert [b.text for b in r.content] == ["0", "1"]
