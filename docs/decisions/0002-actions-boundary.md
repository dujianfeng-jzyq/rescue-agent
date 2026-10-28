# 0002 指令统一走 actions 服务，三道防线

状态：采纳

## 背景

方案文档里“发布预警、下发方案”等动作分散在各模块，人工确认依赖 DSH 的审批机制。问题：确认只在 DSH 一侧，换一个客户端或绕过 DSH 就没有保护；指令也没有统一台账。

## 决定

- 新增 `mcp/actions`，所有“对外产生效果”的工具只在这里：publish_warning、issue_firefighting_plan、issue_sealing_plan、approve_unsealing、issue_dispatch_order、notify_evacuation、escalate_emergency。
- 只有指挥 Agent 挂载 actions（`scripts/check_patches.py` 强制检查）。
- 三道防线：
  1. rescue-approval 插件在 `tools/pre-execute` 调 `/redlines/check` 预检，**失败即拒绝**（预检服务不可达也拒绝）；
  2. 预检通过后走 DSH 的人工确认（ask）；
  3. actions 服务端用同一引擎再校验方案类型、数据时效和红线，不信任调用方。
- 每条成功指令写 `var/actions-ledger.jsonl`；插件另记审计日志，含被拦截和被拒绝的调用。

## 后果

- 模拟矿井环境下，actions 把确认后的处置转发给模拟器，形成闭环；真实矿井下只记台账、通知人员执行。
- 确认人角色目前只在提示中展示；按角色限定确认人需要 DSH 账号/角色能力，记为 TODO。
