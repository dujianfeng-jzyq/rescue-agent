"""fire_sim：外部火灾推演服务（MineFireSimulation）的适配服务，只读、内网（docs/design-v7-fire-sim.md 附录 A.3）。

边界约定：
- 调用方只有证据插件（按请求记录调用、把输入输出存成证据记录）。**任何智能体预设都不挂载本服务**
  （scripts/check_patches.py 检查）：否则智能体可以用自己编的火源和网络调用，得到不受记录约束的“推演结果”。
- 输入、输出都用我方编号；数组下标转换、校验、超时、结果压缩都在这里。结果是“第二意见”，
  不替代我方研判（skills/fire-assessment 的 personnel），也不作为指令依据。这里不做判读（没有阈值）。
- 输入不合法抛 ToolError；提供方服务不可用、超时、报错不抛错，返回 available=false 和原因。

配置：RESCUE_FIRE_SIM_URL（如 http://192.168.0.200:5079，未设置时工具返回“未配置”）、
RESCUE_FIRE_SIM_TOKEN（可选，提供方支持鉴权后用）、RESCUE_FIRE_SIM_TIMEOUT_S（超时，缺省 60 秒）。
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import service
from .adapter import Boundary, Branch, Fire, InputError, Node, SimInput

INSTRUCTIONS = "外部火灾推演服务的适配：按我方编号的通风网络和火源推演风速和 CO 蔓延（第二意见，试验性）。只供证据插件调用。"


def build_server(config: service.Config | None = None, transport: Any = None) -> MCPServer:
    """config 缺省从环境变量读；transport 只给测试（假服务）。"""
    cfg = config or service.Config.from_env()
    server = MCPServer("fire_sim", instructions=INSTRUCTIONS)

    @server.tool()
    async def simulate_fire(nodes: list[Node], branches: list[Branch], fire: Fire, boundary: Boundary) -> dict[str, Any]:
        """推演一次巷道火灾：返回每条巷道每个时间步的风速（air_speed_m_s）和全巷道最大 CO（co_max_ppm，服务给 vol%，已换成 ppm，
        分辨率 10 ppm），以及首次出现 CO 的时刻、CO 峰值、风向是否反转。provider_request 是转换后的服务输入（下标形式），
        result_sha256 是原始返回的摘要（原始返回很大，不带回）。服务不可用时 available 为 false，error 写原因。"""
        inp = SimInput(nodes=nodes, branches=branches, fire=fire, boundary=boundary)
        try:
            return await service.simulate(inp, cfg, transport=transport)
        except InputError as err:
            raise ToolError(f"输入不合法：{err}") from err

    @server.tool()
    async def fire_sim_status() -> dict[str, Any]:
        """外部服务是否可用（服务没有健康检查接口，取接口说明看推演接口在不在）。"""
        ok, detail = await service.health(cfg, transport=transport)
        return {"ok": ok, "service_url": cfg.url, "detail": detail}

    return server
