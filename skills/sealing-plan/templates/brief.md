{{! 封闭方案要点：主预设原样贴进回答的短文（完整方案和审核意见作为文件交付）。数据：compose、review（可缺）的记录内容，extra：steps_text、redline_text、verdict_text、human_count（0 时为 null）、review_id（没有审核记录时为 null）。 }}
【封闭方案要点】{{compose.result.plan.title}}（{{compose.result.plan.plan_id}}）

- 步骤（共 {{compose.result.step_count}} 步）：{{steps_text}}
- 密闭巷道：{{compose.result.seal_walls_text}}；爆炸危险性：{{compose.result.explosion.overall_name}}
- 物资：{{#compose.result.has_shortages}}有缺口的物资 {{compose.result.shortage_count}} 项，需人工联系外部来源{{/compose.result.has_shortages}}{{^compose.result.has_shortages}}台账无缺口{{/compose.result.has_shortages}}
- 红线预检：{{redline_text}}；合规审核：{{verdict_text}}
{{#human_count}}
- 人工确认项：{{.}} 项（见审核意见）
{{/human_count}}

依据：记录 {{record_ids.compose}}（方案）{{#review_id}}、{{.}}（审核，下发时引用）{{/review_id}}；方案结构和参数为开发占位值
