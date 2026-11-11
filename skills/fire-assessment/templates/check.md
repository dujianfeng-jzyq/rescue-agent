{{! 风险检测结果（check.py）的文字格式。数据：panel、as_of_text、record_ids、check。 }}
【风险检测】{{panel}} 工作面 {{as_of_text}}：{{#check.result.anomaly}}发现异常{{/check.result.anomaly}}{{^check.result.anomaly}}未见异常{{/check.result.anomaly}}（与{{check.result.compared_with}}比较）

{{#check.result.reasons}}
- {{.}}
{{/check.result.reasons}}

预警等级：{{check.result.level_name}}；自燃发展阶段：{{check.result.stage_name}}

实时报警：{{^check.result.alarms}}无{{/check.result.alarms}}

{{#check.result.alarms}}
- [{{article}}] {{sensor_id}}：{{message}}
{{/check.result.alarms}}
{{#check.result.failed_required}}
- 必需测点失效：{{.}}
{{/check.result.failed_required}}
{{#check.result.stale_required}}
- 数据中断（超过规定时长没有新数据）：{{.}}
{{/check.result.stale_required}}
{{#check.result.missing_groups}}
- 必需测点组没有匹配的测点：{{.}}
{{/check.result.missing_groups}}
{{#check.result.data_quality}}
- 数据质量：{{.}}
{{/check.result.data_quality}}
{{#check.result.sealed}}
- 火区已封闭：上隅角、回风巷测点在密闭内断电，不作必需测点、不参与分级，按束管监测
{{/check.result.sealed}}

依据：记录 {{record_ids.check}}（检测结果）；检测判据为开发初稿（占位值）
