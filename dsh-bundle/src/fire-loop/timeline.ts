/**
 * 固定流程 review_timeline（第三版 P5）：事件复盘。步骤由代码决定，次数和时长全在 incident-review 技能的脚本里，没有模型阶段。
 *
 *   事件库读出事件条目 → 存为证据记录 → timeline.py → 复盘报告和要点（incident-review/templates）
 */

import { randomInt } from 'node:crypto'

import type { Origin } from '../evidence/records.ts'
import type { Evidence } from '../evidence/service.ts'
import type { IncidentStore } from '../incident/store.ts'
import type { FlowEvent } from './assessment.ts'

export const TIMELINE_FLOW = 'review_timeline'
const IR = 'incident-review'

export class TimelineInputError extends Error {}

export interface TimelineOptions {
  store: IncidentStore
  /** 不给就取最近更新的事件（含已关闭的） */
  incidentId?: string
  origin: Origin
  signal?: AbortSignal
  /** 向矿井查回执的后续状态（rescue-mine 的 get_action_status）；不给就不查，复盘里没有回执部分 */
  receipt?: (mineReceiptId: string) => Promise<unknown>
}

export async function reviewTimeline(ev: Evidence, opts: TimelineOptions) {
  const runId = `TL-${Date.now().toString(36)}-${randomInt(36 ** 4).toString(36).padStart(4, '0')}`
  const events: FlowEvent[] = []
  const emit = (stage: string, status: FlowEvent['status'], recordIds: string[] = [], detail: string | null = null) =>
    events.push({ flow: TIMELINE_FLOW, run_id: runId, stage, status, at: new Date().toISOString(), record_ids: recordIds, detail })

  let id = opts.incidentId
  if (!id) {
    const all = await opts.store.list(true)
    if (!all.length) throw new TimelineInputError('事件库里还没有事件，没有可复盘的内容')
    id = all.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1))[0]!.incident_id
  }
  let view, entries
  try {
    view = await opts.store.get(id)
    entries = await opts.store.entries(id)
  } catch (err) {
    throw new TimelineInputError(`读不到事件 ${id}：${(err as Error).message}`)
  }
  const ids: Record<string, string> = {}
  const step = async <T extends { record_id: string }>(stage: string, fn: () => Promise<T>): Promise<T> => {
    emit(stage, 'started')
    try {
      const r = await fn()
      ids[stage] = r.record_id
      emit(stage, 'completed', [r.record_id])
      return r
    } catch (err) {
      emit(stage, 'failed', [], (err as Error).message)
      throw err
    }
  }
  const lastAt = entries[entries.length - 1]!.at
  // 对外动作的矿井回执：guard 记进时间线的 mine_receipt_ids，逐个向矿井查后续状态；查不到的记下原因，由脚本算“查不到”
  const receipts: Record<string, unknown> = {}
  if (opts.receipt) {
    for (const e of entries) {
      if (e.kind !== 'note' || e.data.type !== 'action') continue
      for (const mid of (e.data.mine_receipt_ids as string[] | undefined) ?? []) {
        if (mid in receipts) continue
        try {
          receipts[mid] = await opts.receipt(mid)
        } catch (err) {
          receipts[mid] = { error: (err as Error).message }
        }
      }
    }
  }
  const evidence = await step('evidence', () =>
    ev.putEvidence({
      skill: IR,
      kind: 'timeline',
      panel: view.panel,
      asOf: lastAt,
      origin: opts.origin,
      origin_of: `incident-store:${id}`,
      content: { incident_id: id, category: view.category, panel: view.panel, as_of: lastAt, entries, receipts },
    }),
  )
  const timeline = await step('timeline', () =>
    ev.runScript({ skill: IR, script: 'timeline', inputs: { evidence: evidence.record_id }, origin: opts.origin, ...(opts.signal ? { signal: opts.signal } : {}) }).then((x) => x.record),
  )
  const report = await step('report', () => ev.renderReport({ skill: IR, template: 'report', inputs: { timeline: timeline.record_id }, origin: opts.origin }))
  const brief = await step('brief', () => ev.renderReport({ skill: IR, template: 'brief', inputs: { timeline: timeline.record_id }, origin: opts.origin }))
  const r = (timeline.content as any).result
  return {
    card: {
      title: `${r.incident_id} 事件复盘`,
      summary: `${r.status_name}；${r.opened_at} 至 ${r.last_at}；${r.counts_text}`,
      record_ids: [report.record_id, timeline.record_id],
      files: [],
      next_actions: [],
      warnings: r.has_unfinished ? [`还有未完成事项：未确认的告警 ${r.unhandled_alert_count} 条，未完成的跟踪计划 ${r.open_followup_count} 个`] : [],
    },
    report_markdown: (report.content as string).trimEnd(),
    brief_markdown: (brief.content as string).trimEnd(),
    record_ids: ids,
    result: {
      incident_id: r.incident_id,
      panel: r.panel,
      status: r.status,
      status_name: r.status_name,
      counts: r.counts,
      counts_text: r.counts_text,
      durations_text: r.durations_text,
      unhandled_alert_count: r.unhandled_alert_count,
      open_followup_count: r.open_followup_count,
      receipts_text: r.has_receipts ? r.receipts_text : null,
    },
    events,
  }
}
