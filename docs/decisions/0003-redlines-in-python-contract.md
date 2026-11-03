# 0003 红线引擎放在 Python 契约包，自查工具放在 kb

状态：采纳；“自查工具放在 kb”已被 [0007](0007-skills-plugins-fixed-flows.md) 取代（2026-11-03）：自查改为 actions 的只读工具 `precheck_plan`，知识库改为 DSH 插件。规则只实现一次仍有效。

## 背景

红线（《矿山救援规程》中的强制性要求）需要在三处判定：方案 Agent 起草时自查、DSH 插件预检、actions 服务端终检。若各写一份，必然漂移。

## 决定

- 规则只实现一次：`rescue_contract.redlines`（规则表 + `check_plan`），`rescue_contract.validation` 负责方案类型、时效和预检入口。
- 判定只看结构化字段（`Plan`、`PlanStep.type`、`FireContext`），不做自然语言理解；未知值一律按不通过处理（fail closed）。
- DSH 插件（TypeScript）不复制规则，只调用 actions 的 `/redlines/check`。
- 起草期自查工具 `check_plan_redlines` 放在 kb 而不是 actions：方案 Agent 和合规 Agent 需要自查，但不应获得下指令的能力。
- `skills/compliance/red-line-rules/SKILL.md` 与规则表有同步测试，防止“说的”和“判的”不一致。

## 后果

- 新增规则 = 改 `redlines.py` + 测试 + 技能说明，一次覆盖三道防线。
- 规则的条文解释必须由专家审定；代码注释里标明对应条号。
