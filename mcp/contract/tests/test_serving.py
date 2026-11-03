"""serving：空列表结果也要有模型可见的 content；令牌按服务拆分、/sim/ 用控制令牌。"""

import asyncio
import json

import pytest
from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from rescue_contract.serving import auth_headers, build_app, ensure_text_content, service_token, sim_control_headers
from starlette.responses import JSONResponse
from starlette.testclient import TestClient


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


# ── 令牌（按服务拆分，/sim/ 用控制令牌）──────────────────────────────────────

TOKEN_VARS = ("RESCUE_MCP_TOKEN", "RESCUE_MCP_MINE_DATA_TOKEN", "RESCUE_MCP_ACTIONS_TOKEN", "RESCUE_SIM_CONTROL_TOKEN")


@pytest.fixture
def env(monkeypatch):
    for k in TOKEN_VARS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _app(name: str):
    server = MCPServer(name)

    @server.custom_route("/sim/state", methods=["GET"])
    async def sim_state(_req):
        return JSONResponse({"truth": "secret"})

    return TestClient(build_app(server))


def _status(client, path: str, token: str | None) -> int:
    return client.get(path, headers={"Authorization": f"Bearer {token}"} if token else {}).status_code


def test_no_tokens_no_auth(env):
    c = _app("mine_data")
    assert _status(c, "/sim/state", None) == 200 and auth_headers() == {} and sim_control_headers() == {}


def test_shared_token_fallback(env):
    env.setenv("RESCUE_MCP_TOKEN", "shared")
    c = _app("mine_data")
    assert _status(c, "/sim/state", None) == 401
    assert _status(c, "/sim/state", "shared") == 200
    assert _status(c, "/healthz", None) == 200
    assert service_token("actions") == "shared"


def test_per_service_token_replaces_shared(env):
    env.setenv("RESCUE_MCP_TOKEN", "shared")
    env.setenv("RESCUE_MCP_ACTIONS_TOKEN", "act")
    c = _app("actions")
    assert _status(c, "/other", "shared") == 401  # 设置了服务令牌后，共用令牌不再有效
    assert _status(c, "/other", "act") == 404
    assert auth_headers("actions") == {"Authorization": "Bearer act"}
    assert auth_headers("mine_data") == {"Authorization": "Bearer shared"}


def test_sim_control_needs_control_token(env):
    env.setenv("RESCUE_MCP_MINE_DATA_TOKEN", "readonly")
    env.setenv("RESCUE_SIM_CONTROL_TOKEN", "control")
    c = _app("mine_data")
    assert _status(c, "/sim/state", "readonly") == 401  # 只读令牌打不开控制接口（看不到真值）
    assert _status(c, "/sim/state", "control") == 200
    assert _status(c, "/other", "control") == 401  # 控制令牌也不能当 MCP 令牌用
    assert _status(c, "/other", "readonly") == 404
    assert sim_control_headers() == {"Authorization": "Bearer control"}


def test_only_control_token_set(env):
    env.setenv("RESCUE_SIM_CONTROL_TOKEN", "control")
    c = _app("mine_data")
    assert _status(c, "/sim/state", None) == 401
    assert _status(c, "/other", None) == 404  # MCP 不鉴权（本机开发），但控制接口仍受保护


def test_sim_prefix_only_for_mine_data(env):
    env.setenv("RESCUE_SIM_CONTROL_TOKEN", "control")
    assert _status(_app("actions"), "/sim/state", None) == 200
