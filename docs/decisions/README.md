# 设计决策记录（ADR）

每条决策一个文件：背景、决定、后果。推翻某条决策时新增一条并在旧文件标注“已被 00xx 取代”，不删除旧文件。

| 编号 | 决策 | 状态 |
| --- | --- | --- |
| [0001](0001-dsh-bundle-not-fork.md) | 以 bundle 扩展 DSH，不 fork | 采纳 |
| [0002](0002-actions-boundary.md) | 指令统一走 actions 服务，三道防线 | 采纳 |
| [0003](0003-redlines-in-python-contract.md) | 红线引擎放在 Python 契约包，自查工具放在 kb | 采纳；自查工具的位置已被 0007 取代（移到 actions） |
| [0004](0004-mcp-transport.md) | MCP 用 streamable-http，地址走环境变量；Python SDK 2.x | 采纳 |
| [0005](0005-simulated-mine-boundary.md) | 模拟矿井与真实矿井共用同一契约 | 采纳 |
| [0006](0006-workflow-vs-agent.md) | 确定性流程用代码工作流（MCP）实现，智能体只做理解、开放问答、经验判断和异常处理；不采用 DSH 自带工作流 | 部分被 0007 取代（工作流的载体和 DSH 插件的结论） |
| [0007](0007-skills-plugins-fixed-flows.md) | 第二版：专家经验以技能为载体，证据、固定流程、知识库做成 DSH 插件，指令发布仍走 actions | 采纳 |
