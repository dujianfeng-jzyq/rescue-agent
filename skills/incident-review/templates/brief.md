{{! 事件复盘要点：主预设原样贴进回答的短文（完整报告作为文件交付）。数据：panel、as_of_text、record_ids、timeline。 }}
【事件复盘要点】{{timeline.result.incident_id}}（{{panel}} 工作面）

- 状态：{{timeline.result.status_name}}；{{timeline.result.opened_at}} 至 {{timeline.result.last_at}}
- {{timeline.result.counts_text}}
- 发现异常到第一次研判：{{timeline.result.durations_text.alert_to_assessment_min}}；到第一次下发成功：{{timeline.result.durations_text.alert_to_issued_min}}
{{#timeline.result.has_receipts}}
- {{timeline.result.receipts_text}}
{{/timeline.result.has_receipts}}
{{#timeline.result.has_unfinished}}
- 未完成事项：未确认的告警 {{timeline.result.unhandled_alert_count}} 条；未完成的跟踪计划 {{timeline.result.open_followup_count}} 个
{{/timeline.result.has_unfinished}}

依据：记录 {{record_ids.timeline}}（事件复盘）
