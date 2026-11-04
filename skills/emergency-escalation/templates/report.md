{{! 升级建议的事实部分（由第一版 mcp/workflow/reports.py 的 escalation 迁移）。数据：panel、as_of_text、record_ids、escalation。 }}
【升级建议】{{panel}} 工作面 {{as_of_text}}

{{#escalation.result.recommend}}
结论：建议升级为应急响应（计算结果）

{{/escalation.result.recommend}}
{{^escalation.result.recommend}}
结论：暂不建议升级为应急响应（计算结果）

{{/escalation.result.recommend}}
预警等级：{{escalation.result.level_name}}；自燃发展阶段：{{escalation.result.stage_name}}

{{#escalation.result.recommend}}
命中的升级判据：

{{#escalation.result.reasons}}
- {{.}}
{{/escalation.result.reasons}}

{{escalation.result.evacuation_reminder}}

{{/escalation.result.recommend}}
{{^escalation.result.recommend}}
升级判据均未命中：

{{#escalation.result.not_met}}
- {{.}}
{{/escalation.result.not_met}}
{{/escalation.result.recommend}}

规程依据（条文原文）：

{{#escalation.result.articles}}
- {{document}} {{article}}：{{content}}
{{/escalation.result.articles}}

依据：记录 {{record_ids.escalation}}（升级建议）；升级判据为开发占位值，是否升级由指挥人员决定
