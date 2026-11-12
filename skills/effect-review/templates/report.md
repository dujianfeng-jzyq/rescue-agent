{{! 效果复评报告的事实部分（由第一版 mcp/workflow/reports.py 的 effect_review 迁移）。数据：panel、as_of_text、record_ids、effect（effect.py 的输出）。 }}
【效果评估】{{panel}} 工作面 {{as_of_text}}

结论：{{effect.result.overall_name}}（计算结果）

{{effect.result.since_label}}：{{effect.result.since}}（{{effect.result.since_source}}）；已观测 {{effect.result.observed_h}} 小时

{{#effect.result.basis}}
- 依据：{{.}}
{{/effect.result.basis}}

各结论测点数：{{effect.result.counts_text}}

{{#effect.result.sealed_off_text}}
火区已封闭、测点断电，不参与复评：{{.}}

{{/effect.result.sealed_off_text}}
{{#effect.result.counts_by_type}}
- {{type}} {{measurand}} 共 {{total}} 个：{{counts_text}}
{{/effect.result.counts_by_type}}

逐点结果（计算结果）：

{{#effect.result.points}}
- {{sensor_id}} {{measurand}}：{{effect_name}}。{{at_label}} {{value_at_since}}，现在 {{latest}}，变化 {{change_since}}；速率 {{before_label}} {{rate_before_per_hour}}/h、{{after_label}} {{rate_after_per_hour}}/h，降幅 {{rate_reduction_pct}}%{{#new_marker_gases_text}}；新出现 {{.}}{{/new_marker_gases_text}}
{{/effect.result.points}}

下次复评：{{effect.result.next_review_at}}（届时已观测 {{effect.result.observed_h_at_next_review}} 小时）

{{#effect.result.escalation_suggested}}
结论为恶化：建议重新研判（run_fire_assessment），由升级建议判断是否升级为应急响应。

{{/effect.result.escalation_suggested}}
依据：记录 {{record_ids.effect}}（效果复评）；判据为开发占位值
