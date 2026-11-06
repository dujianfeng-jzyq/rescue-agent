{{! 启封评估要点：主预设原样贴进回答的短文（完整报告作为文件交付）。数据：panel、as_of_text、record_ids、unseal。 }}
【启封评估要点】{{panel}} 工作面 {{as_of_text}}

- 结论：{{unseal.result.ready_name}}（计算结果）；共 {{unseal.result.total_count}} 项判定：{{unseal.result.met_count}} 项满足，{{unseal.result.unmet_count}} 项不满足
{{#unseal.result.ready}}
- 启封方案共 {{unseal.result.step_count}} 步；下发用 approve_unsealing，record_id 照抄下面的记录编号，由总指挥确认
{{/unseal.result.ready}}
{{^unseal.result.ready}}
- 不满足的项：{{unseal.result.unmet_labels}}；条件满足前不生成启封方案
{{/unseal.result.ready}}

依据：记录 {{record_ids.unseal}}（启封评估，下发时引用）；判据为开发占位值
