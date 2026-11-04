{{! 阶段“研判说明”的输出（模型只从清单里选，说明文字经代码检查数值）。数据：options（explanation_options 的结果记录）、explanation。 }}
【研判说明】（由模型从给定清单中选择，说明文字中的数值已核对来自本次记录）

{{explanation.summary}}

关注点：

{{#explanation.focus}}
- {{label}}{{#note}}——{{.}}{{/note}}
{{/explanation.focus}}

建议的下一步（由指挥人员决定，需发布的指令一律人工确认）：

{{#explanation.next_steps}}
- {{label}}{{#reason}}——{{.}}{{/reason}}
{{/explanation.next_steps}}

依据：记录 {{record_ids.options}}（选择清单）
