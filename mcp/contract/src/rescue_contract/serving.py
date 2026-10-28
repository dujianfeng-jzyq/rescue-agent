"""所有 MCP 服务器共用的启动方式：Streamable HTTP + 可选的静态令牌校验。

DSH 的 MCP 客户端只发送静态请求头，所以服务间认证用静态 Bearer 令牌：
设置环境变量 RESCUE_MCP_TOKEN 后，所有请求必须带 `Authorization: Bearer <令牌>`；
不设置则不校验（仅限本机开发）。
"""

from __future__ import annotations

import hmac
import logging
import os

import uvicorn
from mcp.server.mcpserver import MCPServer
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


class BearerTokenMiddleware:
    """令牌校验。/healthz 不需要令牌，便于容器探活。"""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") != "/healthz":
            headers = dict(scope.get("headers") or [])
            got = headers.get(b"authorization", b"")
            if not hmac.compare_digest(got, self.expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_app(server: MCPServer) -> ASGIApp:
    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_request):
        return JSONResponse({"ok": True, "server": server.name})

    app: ASGIApp = server.streamable_http_app(stateless_http=True, json_response=True)
    token = os.environ.get("RESCUE_MCP_TOKEN")
    if token:
        app = BearerTokenMiddleware(app, token)
    return app


def serve(server: MCPServer, key: str) -> None:
    """启动服务。端口取 RESCUE_<KEY>_PORT，默认见 DEFAULT_PORTS。"""
    logging.basicConfig(level=os.environ.get("RESCUE_LOG_LEVEL", "INFO"))
    port = int(os.environ.get(f"RESCUE_{key.upper()}_PORT", DEFAULT_PORTS[key]))
    host = os.environ.get("RESCUE_MCP_HOST", "127.0.0.1")
    if not os.environ.get("RESCUE_MCP_TOKEN"):
        log.warning("RESCUE_MCP_TOKEN 未设置，%s 不校验令牌（仅限本机开发）", key)
    uvicorn.run(build_app(server), host=host, port=port, log_level="warning")
