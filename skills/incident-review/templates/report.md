{{! 事件复盘报告的事实部分。数据：panel、as_of_text、record_ids、timeline（timeline.py 的输出）。 }}
【事件复盘】{{timeline.result.incident_id}}（{{panel}} 工作面）{{as_of_text}}

状态：{{timeline.result.status_name}}；事件开始 {{timeline.result.opened_at}}，最近一条 {{timeline.result.last_at}}

{{timeline.result.counts_text}}

响应时长（计算结果）：

- 发现异常到第一次研判：{{timeline.result.durations_text.alert_to_assessment_min}}
- 发现异常到第一次对外动作（含被拒绝、失败的）：{{timeline.result.durations_text.alert_to_action_min}}
- 发现异常到第一次下发成功：{{timeline.result.durations_text.alert_to_issued_min}}
- 事件开始到最近一条：{{timeline.result.durations_text.opened_to_last_min}}

{{#timeline.result.has_unfinished}}
未完成事项：未确认的告警 {{timeline.result.unhandled_alert_count}} 条；未完成的跟踪计划 {{timeline.result.open_followup_count}} 个

{{/timeline.result.has_unfinished}}
{{#timeline.result.has_receipts}}
矿井回执（复盘时向矿井查到的后续状态，计算结果）：{{timeline.result.receipts_text}}

{{#timeline.result.receipt_rows}}
- {{tool}} {{action_id}} → 矿井回执 {{mine_id}}：{{kind_name}}{{status_name}}{{#has_detail}}（{{detail}}）{{/has_detail}}
{{/timeline.result.receipt_rows}}

{{/timeline.result.has_receipts}}
时间线：

{{#timeline.result.rows}}
- {{at}} {{label}}（{{by}}）{{text}}
{{/timeline.result.rows}}

依据：记录 {{record_ids.timeline}}（事件复盘）；时间线来自事件库，对外动作以指令台账为准
