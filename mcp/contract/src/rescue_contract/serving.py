"""所有 MCP 服务器共用的启动方式：Streamable HTTP + 可选的静态令牌校验。

DSH 的 MCP 客户端只发送静态请求头，所以服务间认证用静态 Bearer 令牌：
设置环境变量 RESCUE_MCP_TOKEN 后，所有请求必须带 `Authorization: Bearer <令牌>`；
不设置则不校验（仅限本机开发）。
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
}


def auth_headers() -> dict[str, str]:
    """服务间 HTTP 调用要带的认证头（与 BearerTokenMiddleware 对应）；未设置令牌时为空。"""
    token = os.environ.get("RESCUE_MCP_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


class BearerTokenMiddleware:
    """令牌校验。/healthz 不需要令牌，便于容器探活。

    `public_prefixes` 下的路径也不校验：只用于凭不可猜测的文件名下载的生成文件（如 fire_model 的 /charts/），
    浏览器点下载链接时带不上令牌。
    """

    def __init__(self, app: ASGIApp, token: str, public_prefixes: tuple[str, ...] = ()) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()
        self.public_prefixes = public_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path") or ""
        if scope["type"] == "http" and path != "/healthz" and not path.startswith(self.public_prefixes):
            headers = dict(scope.get("headers") or [])
            got = headers.get(b"authorization", b"")
            if not hmac.compare_digest(got, self.expected):
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


def build_app(server: MCPServer, public_prefixes: tuple[str, ...] = ()) -> ASGIApp:
    ensure_text_content(server)

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_request):
        return JSONResponse({"ok": True, "server": server.name})

    app: ASGIApp = server.streamable_http_app(stateless_http=True, json_response=True)
    token = os.environ.get("RESCUE_MCP_TOKEN")
    if token:
        app = BearerTokenMiddleware(app, token, public_prefixes)
    return app


def serve(server: MCPServer, key: str, public_prefixes: tuple[str, ...] = ()) -> None:
    """启动服务。端口取 RESCUE_<KEY>_PORT，默认见 DEFAULT_PORTS。"""
    logging.basicConfig(level=os.environ.get("RESCUE_LOG_LEVEL", "INFO"))
    port = int(os.environ.get(f"RESCUE_{key.upper()}_PORT", DEFAULT_PORTS[key]))
    host = os.environ.get("RESCUE_MCP_HOST", "127.0.0.1")
    if not os.environ.get("RESCUE_MCP_TOKEN"):
        log.warning("RESCUE_MCP_TOKEN 未设置，%s 不校验令牌（仅限本机开发）", key)
    uvicorn.run(build_app(server, public_prefixes), host=host, port=port, log_level="warning")
