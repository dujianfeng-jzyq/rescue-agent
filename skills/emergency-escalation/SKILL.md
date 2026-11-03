---
name: emergency-escalation
description: 是否建议把采空区自燃从预警升级为应急响应：按预设判据给出建议、命中的判据和规程条文。
whenToUse: 指挥人员问“要不要升级”“是否启动应急响应”，或研判结果为红色预警、激烈氧化、处置效果恶化、出现实时红线报警时。
version: 0.1.0
---

# 升级应急响应

> 状态：草稿，待专家按本矿火灾专项应急预案审定。判据在 `references/escalation.yaml`，全部是开发占位值。

## 怎么做

1. 升级建议是完整研判（`run_fire_assessment`）的一部分：报告末尾的“升级建议”就是本技能脚本的结果，照抄。
2. 建议升级时，报告会给出命中的判据和撤人提醒。**撤人不等待本建议**：一旦进入应急响应，调度员按预案立即撤人。
3. 指挥人员决定升级后，用 actions 的 `escalate_emergency` 提交，引用研判的记录编号；会弹出人工确认。
4. 不建议升级时，照抄“升级判据均未命中”的逐项说明，不要自己归纳。

## 判据（满足任一即建议升级）

- 预警等级达到 `level_at_least`；
- 自燃发展阶段属于 `stages`；
- 最近一次处置效果评估为“恶化”（有效果评估时）；
- 有实时红线报警。

## 脚本

`scripts/escalation.py`：输入 `{"warning", "stage", "alarms", "effect"?}`，各为 fire-assessment（及 effect-review）相应脚本的输出。

## 边界

是否升级由指挥人员决定；本技能只给建议和依据。条文引自本技能的 `references/articles.yaml`（本地留存），以后由知识库按条号取原文。
