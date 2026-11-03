"""所有 MCP 服务器共用的启动方式：Streamable HTTP + 可选的静态令牌校验。

DSH 的 MCP 客户端只发送静态请求头，所以服务间认证用静态 Bearer 令牌（docs/design-v2.md 7.1）：
- 每个服务认自己的令牌 RESCUE_MCP_<服务>_TOKEN（如 RESCUE_MCP_MINE_DATA_TOKEN、RESCUE_MCP_ACTIONS_TOKEN）；
  没有设置时退回共用的 RESCUE_MCP_TOKEN（第一版和本机开发）；都没有则不校验（仅限本机开发）。
- 模拟矿井的 /sim/ 控制接口认 RESCUE_SIM_CONTROL_TOKEN（没有时同 mine_data 的令牌）。设置后，mine_data 的只读令牌
  打不开控制接口：证据插件（DSH）只拿只读令牌，读不到真值、改不了模拟器（CLAUDE.md 约定 4）。
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("rescue")

# 端口约定。真实矿井适配器沿用同一端口和路径，切换时只需替换进程。
DEFAULT_PORTS = {
    "mine_data": 8101,
    "ventnet": 8102,
    "fire_model": 8103,
    "dispatch": 8104,
    "kb": 8105,
    "actions": 8106,
    "workflow": 8107,
}


SHARED_TOKEN_ENV = "RESCUE_MCP_TOKEN"
SIM_CONTROL_TOKEN_ENV = "RESCUE_SIM_CONTROL_TOKEN"
SIM_CONTROL_PREFIX = "/sim/"


def token_env(service: str) -> str:
    return f"RESCUE_MCP_{service.upper()}_TOKEN"


def service_token(service: str) -> str | None:
    """某个服务认的令牌：RESCUE_MCP_<服务>_TOKEN，没有则 RESCUE_MCP_TOKEN；空字符串当作没有。"""
    return os.environ.get(token_env(service)) or os.environ.get(SHARED_TOKEN_ENV) or None


def sim_control_token() -> str | None:
    return os.environ.get(SIM_CONTROL_TOKEN_ENV) or service_token("mine_data")


def _bearer(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def auth_headers(service: str = "mine_data") -> dict[str, str]:
    """调用某个服务要带的认证头（与 BearerTokenMiddleware 对应）；未设置令牌时为空。"""
    return _bearer(service_token(service))


def sim_control_headers() -> dict[str, str]:
    """调用模拟矿井 /sim/ 控制接口要带的认证头（actions 转发处置、控制台、评测）。"""
    return _bearer(sim_control_token())


class BearerTokenMiddleware:
    """令牌校验。/healthz 不需要令牌，便于容器探活。

    `path_tokens`：某些路径前缀用另外的令牌（如 mine_data 的 /sim/ 用控制令牌），只认那个令牌。
    `public_prefixes` 下的路径不校验：只用于凭不可猜测的文件名下载的生成文件（如 fire_model 的 /charts/），
    浏览器点下载链接时带不上令牌。`token` 为 None 时，除 path_tokens 外都不校验。
    """

    def __init__(self, app: ASGIApp, token: str | None, public_prefixes: tuple[str, ...] = (), path_tokens: dict[str, str] | None = None) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode() if token else None
        self.public_prefixes = public_prefixes
        self.path_tokens = {p: f"Bearer {t}".encode() for p, t in (path_tokens or {}).items()}

    def _expected_for(self, path: str) -> bytes | None:
        for prefix, expected in self.path_tokens.items():
            if path.startswith(prefix):
                return expected
        if path == "/healthz" or (self.public_prefixes and path.startswith(self.public_prefixes)):
            return None
        return self.expected

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            expected = self._expected_for(scope.get("path") or "")
            if expected is not None:
                got = dict(scope.get("headers") or []).get(b"authorization", b"")
                if not hmac.compare_digest(got, expected):
                    await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def ensure_text_content(server: MCPServer) -> MCPServer:
    """工具结果的 content 为空时，用 structured_content 的 JSON 补上。

    SDK 把返回的列表逐项转成 content，空列表就得到空 content；DSH 只把 content 给模型看，
    模型就分不清“没有报警”和“没拿到数据”。这里包装公开方法 `call_tool`
    （SDK 的请求处理按实例属性调用它）。
    """
    if getattr(server, "_rescue_text_fallback", False):
        return server
    call_tool = server.call_tool

    async def call_tool_with_text(name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        result = await call_tool(name, arguments, context)
        if isinstance(result, CallToolResult) and not result.content and result.structured_content is not None:
            text = json.dumps(result.structured_content, ensure_ascii=False, indent=2)
            result = result.model_copy(update={"content": [TextContent(type="text", text=text)]})
        return result

    server.call_tool = call_tool_with_text  # type: ignore[method-assign]
    server._rescue_text_fallback = True  # type: ignore[attr-defined]
    return server


def build_app(server: MCPServer, public_prefixes: tuple[str, ...] = (), key: str | None = None) -> ASGIApp:
    """key 是服务名（缺省取 server.name），决定认哪个令牌；mine_data 的 /sim/ 另认控制令牌。"""
    key = key or server.name
    ensure_text_content(server)

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_request):
        return JSONResponse({"ok": True, "server": server.name})

    app: ASGIApp = server.streamable_http_app(stateless_http=True, json_response=True)
    token = service_token(key)
    path_tokens = {}
    if key == "mine_data" and (control := sim_control_token()):
        path_tokens[SIM_CONTROL_PREFIX] = control
    if token or path_tokens:
        app = BearerTokenMiddleware(app, token, public_prefixes, path_tokens)
    return app


def serve(server: MCPServer, key: str, public_prefixes: tuple[str, ...] = ()) -> None:
    """启动服务。端口取 RESCUE_<KEY>_PORT，默认见 DEFAULT_PORTS。"""
    logging.basicConfig(level=os.environ.get("RESCUE_LOG_LEVEL", "INFO"))
    port = int(os.environ.get(f"RESCUE_{key.upper()}_PORT", DEFAULT_PORTS[key]))
    host = os.environ.get("RESCUE_MCP_HOST", "127.0.0.1")
    if not service_token(key):
        log.warning("%s 和 %s 都未设置，%s 不校验令牌（仅限本机开发）", token_env(key), SHARED_TOKEN_ENV, key)
    uvicorn.run(build_app(server, public_prefixes, key), host=host, port=port, log_level="warning")
