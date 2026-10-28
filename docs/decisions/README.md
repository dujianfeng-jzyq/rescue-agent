# 设计决策记录（ADR）

每条决策一个文件：背景、决定、后果。推翻某条决策时新增一条并在旧文件标注“已被 00xx 取代”，不删除旧文件。

| 编号 | 决策 | 状态 |
| --- | --- | --- |
| [0001](0001-dsh-bundle-not-fork.md) | 以 bundle 扩展 DSH，不 fork | 采纳 |
| [0002](0002-actions-boundary.md) | 指令统一走 actions 服务，三道防线 | 采纳 |
| [0003](0003-redlines-in-python-contract.md) | 红线引擎放在 Python 契约包，自查工具放在 kb | 采纳 |
| [0004](0004-mcp-transport.md) | MCP 用 streamable-http，地址走环境变量；Python SDK 2.x | 采纳 |
| [0005](0005-simulated-mine-boundary.md) | 模拟矿井与真实矿井共用同一契约 | 采纳 |
