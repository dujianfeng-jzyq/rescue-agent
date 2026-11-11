{{! 效果复评要点：主预设原样贴进回答的短文（完整报告作为文件交付）。数据：panel、as_of_text、record_ids、effect，extra：无。 }}
【效果评估要点】{{panel}} 工作面 {{as_of_text}}

- 结论：{{effect.result.overall_name}}（计算结果）
- 处置开始：{{effect.result.since}}（{{effect.result.since_source}}）；已观测 {{effect.result.observed_h}} 小时
- 各结论测点数：{{effect.result.counts_text}}
{{#effect.result.sealed_off_text}}
- 火区已封闭、测点断电，不参与复评：{{.}}
{{/effect.result.sealed_off_text}}
- 下次复评：{{effect.result.next_review_at}}
{{#effect.result.escalation_suggested}}
- 结论为恶化：建议重新研判（run_fire_assessment），由升级建议判断是否升级为应急响应
{{/effect.result.escalation_suggested}}

依据：记录 {{record_ids.effect}}（效果复评）；判据为开发占位值
