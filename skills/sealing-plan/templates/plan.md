{{! 封闭方案文本（事实部分由代码生成）。数据：panel、as_of_text、record_ids、compose（compose.py 的输出）、articles（知识库按方案依据的条号取的原文）。 }}
【封闭方案】{{compose.result.plan.title}}（{{compose.result.plan.plan_id}}）

现场条件（截至 {{as_of_text}}，代码生成，不能手改）：

- 火灾类型：采空区自燃
- 灾区甲烷：{{compose.result.plan.context.methane_pct}} %；是否继续上升：{{compose.result.plan.context.methane_rising}}
- 氧浓度：{{compose.result.plan.context.o2_pct}} %{{#compose.result.explosion.worst_sensor_id}}（{{.}}）{{/compose.result.explosion.worst_sensor_id}}
- 爆炸危险性：{{compose.result.explosion.overall_name}}（爆炸三角形，多测点取最不利）
- 火区封闭时间：{{compose.result.plan.context.sealed_at}}
- 灾区温度：{{compose.result.plan.context.area_temperature_c}} ℃；能见度：{{compose.result.plan.context.visibility_m}} 米
- 火源是否明确：{{compose.result.plan.context.fire_source_confirmed}}；水源、人力和物力是否充足：{{compose.result.plan.context.water_supply_sufficient}}；回风道是否畅通：{{compose.result.plan.context.return_airway_clear}}

{{#compose.result.has_human_inputs}}
人工确认项（系统无法核实，由确认人把关）：

{{#compose.result.human_inputs}}
- {{label}}：{{value}}（确认人：{{confirmed_by}}）
{{/compose.result.human_inputs}}

{{/compose.result.has_human_inputs}}
{{#compose.result.summary}}
方案概述：{{.}}

{{/compose.result.summary}}
步骤（共 {{compose.result.step_count}} 步）：

{{#compose.result.steps_view}}
{{order}}. 【{{type_name}}】{{description}}{{#roadways_text}}（巷道：{{.}}）{{/roadways_text}}{{#params_text}}（参数：{{.}}）{{/params_text}}
{{/compose.result.steps_view}}

材料与库存（计算结果，参数为开发占位值）：{{^compose.result.supply}}无{{/compose.result.supply}}

{{#compose.result.supply}}
- {{label}}：{{#continuous}}总量 {{required}}，台账现有 {{on_hand}}，持续供应（按注浆站能力连续制浆，不算缺口）{{/continuous}}{{^continuous}}需要 {{required}}，台账现有 {{on_hand}}{{^sufficient}}，缺口 {{shortage}}{{/sufficient}}{{/continuous}}
{{/compose.result.supply}}

{{#compose.result.has_dispatch}}
需调运（计算结果，下发方案后按此提交调度单）：

{{/compose.result.has_dispatch}}
{{#compose.result.dispatch_lines}}
- {{label}}：{{quantity}}，{{from_node}} → {{to_node}}
{{/compose.result.dispatch_lines}}
{{#compose.result.has_dispatch}}

{{/compose.result.has_dispatch}}
{{#compose.result.has_shortages}}
有缺口的物资 {{compose.result.shortage_count}} 项，需人工联系外部来源。

{{/compose.result.has_shortages}}
{{#compose.result.has_missing}}
待补充：

{{#compose.result.missing_inputs}}
- {{label}}：{{note}}
{{/compose.result.missing_inputs}}

{{/compose.result.has_missing}}
方案依据（条文原文）：

{{#articles}}
- {{document}} {{article}}：{{#content}}{{.}}{{/content}}{{^content}}（条文索引里没有这一条）{{/content}}
{{/articles}}

依据：记录 {{record_ids.compose}}（方案）；方案结构和材料参数为开发占位值，封闭范围、密闭位置以现场侦察为准
