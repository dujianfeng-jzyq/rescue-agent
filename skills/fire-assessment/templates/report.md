{{! 研判报告的事实部分（由第一版 mcp/workflow/reports.py 的 assessment 迁移）。数值原样填入，不取整、不加“约”；缺失就写缺失。 }}
{{! 数据：panel、as_of_text、record_ids、warning、stage、alarms、trend、explosion、personnel（各为结果记录的内容）、chart_markdown。 }}
【预警研判】{{panel}} 工作面 {{as_of_text}}

结论：预警等级 {{warning.result.level_name}}（计算结果）

自燃发展阶段：{{stage.result.stage_name}}（计算结果）

{{#stage.result.stage_basis}}
- 依据：{{.}}
{{/stage.result.stage_basis}}

实时报警：{{^alarms.result.alarms}}无{{/alarms.result.alarms}}

{{#alarms.result.alarms}}
- [{{article}}] {{sensor_id}}：{{message}}
{{/alarms.result.alarms}}

触发条件：{{^warning.result.triggered}}无{{/warning.result.triggered}}

{{#warning.result.triggered}}
- {{.}}
{{/warning.result.triggered}}

关键指标（计算结果）：

{{#warning.result.indicator_list}}
- {{label}}：{{value}}{{#sensor_id}}（{{.}}）{{/sensor_id}}
{{/warning.result.indicator_list}}

趋势（计算结果）：{{^trend.result.trends}}无{{/trend.result.trends}}

{{#trend.result.trends}}
- {{sensor_id}} {{measurand}} 近 {{hours}} 小时：{{first}}（{{first_ts}}）→ {{latest}}（{{latest_ts}}），变化 {{change}}，速率 {{rate_per_hour}}/h
{{/trend.result.trends}}

爆炸危险性（多组分爆炸三角形，计算结果）：{{explosion.result.overall_name}}

{{#explosion.result.points}}
- {{sensor_id}}：{{zone}} 区 {{zone_name}}。{{meaning}}
{{/explosion.result.points}}
{{#explosion.result.failures}}
- {{sensor_id}}：未能计算。{{error}}
{{/explosion.result.failures}}

{{#personnel.result.computed}}
受威胁人员（通风网络估算，未做风量解算）：受威胁 {{personnel.result.total_threatened}} 人，井下共 {{personnel.result.total_underground}} 人

{{#personnel.result.threatened}}
- {{roadway_id}}：{{count}} 人（{{persons_text}}），有害气体到达 {{arrival_min}} 分钟；{{#escape_route.reachable}}避灾路线至 {{escape_route.target_name}}，{{escape_route.length_m}} 米，佩戴自救器步行 {{escape_route.walk_with_self_rescuer_min}} 分钟{{/escape_route.reachable}}{{^escape_route.reachable}}{{escape_route.note}}{{/escape_route.reachable}}
{{/personnel.result.threatened}}

撤人按预案立即执行，不等待本结果；以上名单是补充核对用的。

{{/personnel.result.computed}}
{{^personnel.result.computed}}
受威胁人员：{{personnel.result.note}}

{{/personnel.result.computed}}
数据质量：{{^trend.result.data_quality_all}}无异常{{/trend.result.data_quality_all}}

{{#trend.result.data_quality_all}}
- {{.}}
{{/trend.result.data_quality_all}}
{{#warning.result.notes}}
- 说明：{{.}}
{{/warning.result.notes}}

依据：记录 {{record_ids.evidence}}（证据）、{{record_ids.warning}}（预警等级）、{{record_ids.stage}}（自燃阶段）、{{record_ids.alarms}}（实时报警）、{{record_ids.trend}}（趋势）、{{record_ids.explosion}}（爆炸三角形）、{{record_ids.personnel}}（受威胁人员）；阈值和判据为开发占位值

{{#chart_markdown}}

{{chart_markdown}}

{{/chart_markdown}}
