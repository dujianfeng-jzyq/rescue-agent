{{! 研判要点：主预设原样贴进回答的短文（完整报告作为文件交付，网页端在右侧预览）。数据：panel、as_of_text、record_ids、warning、stage、alarms、explosion、personnel、escalation（结果记录内容），extra：worst（最不利测点）、articles_text、explanation_summary、chart_markdown。 }}
【研判要点】{{panel}} 工作面 {{as_of_text}}

- 预警等级：{{warning.result.level_name}}；自燃发展阶段：{{stage.result.stage_name}}
- 实时报警：{{^alarms.result.alarms}}无{{/alarms.result.alarms}}{{#alarms.result.alarms}}[{{article}}] {{sensor_id}} {{/alarms.result.alarms}}
{{#alarms.result.reseal_indicated}}
- 启封后复燃：火区已启封且出现复燃征兆，应重新封闭
{{/alarms.result.reseal_indicated}}
- 爆炸危险性：{{explosion.result.overall_name}}
{{#worst}}
- 最不利测点：{{sensor_id}}，{{zone}} 区 {{zone_name}}
{{/worst}}
- 受威胁人员：{{#personnel.result.computed}}{{personnel.result.total_threatened}} 人（井下共 {{personnel.result.total_underground}} 人），撤人按预案立即执行{{/personnel.result.computed}}{{^personnel.result.computed}}{{personnel.result.note}}{{/personnel.result.computed}}
- 升级建议：{{#escalation.result.recommend}}建议升级为应急响应{{/escalation.result.recommend}}{{^escalation.result.recommend}}暂不建议升级为应急响应{{/escalation.result.recommend}}
- 升级建议的规程依据：{{articles_text}}；条文原文见完整报告
{{#explanation_summary}}
- 研判说明：{{.}}
{{/explanation_summary}}

{{#chart_markdown}}
{{chart_markdown}}

{{/chart_markdown}}
依据：记录 {{record_ids.warning}}（预警等级）、{{record_ids.escalation}}（升级建议）；阈值和判据为开发占位值
