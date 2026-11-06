{{! 启封条件判断与启封方案报告的事实部分。数据：panel、as_of_text、record_ids、unseal（unsealing_plan.py 的输出）。 }}
【启封评估】{{panel}} 工作面 {{as_of_text}}

结论：{{unseal.result.ready_name}}（计算结果）；共 {{unseal.result.total_count}} 项判定：{{unseal.result.met_count}} 项满足，{{unseal.result.unmet_count}} 项不满足

逐项判定（计算结果）：

{{#unseal.result.rows}}
- {{text}}
{{/unseal.result.rows}}

{{#unseal.result.ready}}
启封方案（共 {{unseal.result.step_count}} 步，顺序固定，满足第164条：先检查、断电、撤人）：

{{#unseal.result.steps_view}}
{{order}}. {{description}}{{#roadways_text}}（巷道：{{.}}）{{/roadways_text}}
{{/unseal.result.steps_view}}

下发启封方案用 approve_unsealing，只给 record_id；由总指挥确认后执行，guard 执行前还会再核对记录和红线。
{{/unseal.result.ready}}
{{^unseal.result.ready}}
不具备启封条件的项：{{unseal.result.unmet_labels}}。条件满足前不生成启封方案，也不能提交启封审批。
{{/unseal.result.ready}}

依据：记录 {{record_ids.unseal}}（启封评估）；判据和步骤为开发占位值
