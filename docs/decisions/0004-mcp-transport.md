# 0004 MCP 用 streamable-http，地址走环境变量；Python SDK 2.x

状态：采纳

## 背景

方案文档示例使用 FastMCP 和 stdio。实际部署中 MCP 服务可能分布在不同机器（数据接入靠近矿井网络，知识库靠近 RAGFlow），也需要被 DSH 以外的工具（冒烟测试、评测、调试）直接访问。

## 决定

- 所有服务用 streamable-http（无状态、JSON 响应），端口 8101–8106，`/healthz` 健康检查。
- 地址在预设里写成 `process.env.RESCUE_MCP_*_URL ?? 默认值`，部署时只改环境变量。
- 可选静态令牌 `RESCUE_MCP_TOKEN`（Bearer），多机部署必须开启。
- Python SDK 用 `mcp` 2.x 的 `MCPServer`（2.x 已无 FastMCP 名称）；工具返回 pydantic 模型或 `dict[str, Any]`，确保有结构化输出。

## 后果

- 服务可独立启停、独立测试；`scripts/smoke.py` 和 `eval/` 直接连服务或进程内调用。
- 令牌是静态共享密钥，只适合内网；正式部署需换成网关或 mTLS（TODO）。
