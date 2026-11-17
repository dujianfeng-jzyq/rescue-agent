"""route_planner：外部路径规划服务（mine-route-api）的适配服务，只读、内网（docs/design-v6-route-service.md 附录 A.3）。

边界约定：
- 调用方只有证据插件（按请求记录编排调用、把输入输出存成证据记录）。**任何智能体预设都不挂载本服务**
  （scripts/check_patches.py 检查）：否则智能体可以用自己编的输入调用，得到不受记录约束的“路线”。
- 输入、输出都用我方编号；数组下标转换、校验、超时、逐终点调用都在这里。服务的结果是“第二意见”，
  不替代我方路线（skills/fire-assessment 的 personnel），也不作为指令依据。
- 输入不合法抛 ToolError；提供方服务不可用、超时、报错不抛错，返回 available=false 和原因。

配置：RESCUE_ROUTE_SERVICE_URL（如 http://192.168.0.200:8080，未设置时工具返回“未配置”）、
RESCUE_ROUTE_SERVICE_TOKEN（可选，提供方支持鉴权后用）、RESCUE_ROUTE_SERVICE_TIMEOUT_S（每次调用的超时，缺省 5 秒）。
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import service
from .adapter import Edge, InputError, Node, PlanInput

INSTRUCTIONS = "外部路径规划服务的适配：按我方编号的通风网络计算撤离路线（第二意见，试验性）。只供证据插件调用。"


def build_server(config: service.Config | None = None, transport: Any = None) -> MCPServer:
    """config 缺省从环境变量读；transport 只给测试（假服务）。"""
    cfg = config or service.Config.from_env()
    server = MCPServer("route_planner", instructions=INSTRUCTIONS)

    @server.tool()
    async def plan_escape_routes(
        nodes: list[Node], edges: list[Edge], start: str, targets: list[str], walk_speed: float, max_smoke_time: float
    ) -> dict[str, Any]:
        """从起点到各终点（地面出口、避难硐室）的撤离路线：逐个终点调用外部服务，按服务的排序规则
        （暂时脱险时间 → 烟中时间 → 当量距离）选出最优终点（best）。targets 里每个终点的结果（含服务原始返回）都带上，
        provider_request 是转换后的服务输入（下标形式，不含 target）。服务不可用时 available 为 false，error 写原因。"""
        inp = PlanInput(nodes=nodes, edges=edges, start=start, targets=targets, walk_speed=walk_speed, max_smoke_time=max_smoke_time)
        try:
            return await service.plan(inp, cfg, transport=transport)
        except InputError as err:
            raise ToolError(f"输入不合法：{err}") from err

    @server.tool()
    async def route_service_status() -> dict[str, Any]:
        """外部服务的健康检查（GET /healthz）。"""
        ok, detail = await service.health(cfg, transport=transport)
        return {"ok": ok, "service_url": cfg.url, "detail": detail}

    return server
