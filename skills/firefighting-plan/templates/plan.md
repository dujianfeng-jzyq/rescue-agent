{{! 防灭火处置方案文本（事实部分由代码生成）。数据：panel、as_of_text、record_ids、compose（compose.py 的输出）、articles（知识库按方案依据的条号取的原文）。 }}
【防灭火处置方案】{{compose.result.plan.title}}（{{compose.result.plan.plan_id}}）

本方案不封闭火区、不安排人员进入灾区；撤人按预案和升级流程另行处理。

现场条件（截至 {{as_of_text}}，代码生成，不能手改）：

- 火灾类型：采空区自燃
- 灾区甲烷：{{compose.result.plan.context.methane_pct}} %；是否继续上升：{{compose.result.plan.context.methane_rising}}
- 氧浓度：{{compose.result.plan.context.o2_pct}} %{{#compose.result.explosion.worst_sensor_id}}（{{.}}）{{/compose.result.explosion.worst_sensor_id}}
- 爆炸危险性：{{compose.result.explosion.overall_name}}（爆炸三角形，多测点取最不利）

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

依据：记录 {{record_ids.compose}}（方案）；方案结构和材料参数为开发占位值，处置效果由复评判定
