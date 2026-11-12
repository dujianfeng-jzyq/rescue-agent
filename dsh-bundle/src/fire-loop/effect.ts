/**
 * 固定流程 review_effect（design-v2 4.4）：处置效果复评。步骤由代码决定，判据全在 effect-review 技能里，没有模型阶段。
 *
 *   处置开始时间 since：调用方给出；否则取 actions 台账里本工作面最近一次已下发的封闭方案或防灭火方案的下发时间，没有方案时取加密监测
 *   （这时没有处置：since 是观察起点，effect.py 按“未采取处置”写结论，treated=false）
 *   → 采集证据（effect-review 的 effect 清单，窗口从 since 之前 baseline 小时取到现在）
 *   → effect.py → 复评报告（effect-review/templates/report.md）
 */

import { randomInt } from 'node:crypto'

import type { Origin } from '../evidence/records.ts'
import type { Evidence } from '../evidence/service.ts'
import type { FlowEvent } from './assessment.ts'

export const EFFECT_FLOW = 'review_effect'
const ER = 'effect-review'

export interface LastIssued {
  found: boolean
  tool?: string
  action_id?: string
  issued_at?: string
  plan_id?: string
  title?: string
}

export interface EffectOptions {
  panel: string
  since?: string
  origin: Origin
  signal?: AbortSignal
  /** 指令台账里本工作面最近一次已下发的方案（actions 的 query_last_issued） */
  lastIssued: (panel: string) => Promise<LastIssued>
}

/** 调用方能处理的错误（缺处置开始时间等），工具把它作为说明返回 */
export class EffectInputError extends Error {}

const NAMES: Record<string, string> = { issue_sealing_plan: '封闭方案', issue_firefighting_plan: '防灭火方案', start_monitoring: '加密监测' }

export async function reviewEffect(ev: Evidence, opts: EffectOptions) {
  const runId = `ER-${Date.now().toString(36)}-${randomInt(36 ** 4).toString(36).padStart(4, '0')}`
  const events: FlowEvent[] = []
  const emit = (stage: string, status: FlowEvent['status'], recordIds: string[] = [], detail: string | null = null) =>
    events.push({ flow: EFFECT_FLOW, run_id: runId, stage, status, at: new Date().toISOString(), record_ids: recordIds, detail })
  const origin = opts.origin

  let since = opts.since
  let sinceSource = '指挥人员给出'
  // 台账里只有加密监测、没有方案：没有采取处置，复评说的是趋势（2026-11-11 演示复看）
  let treated = true
  if (!since) {
    emit('since', 'started')
    const last = await opts.lastIssued(opts.panel)
    if (!last.found || !last.issued_at) {
      emit('since', 'failed', [], '台账里没有已下发的方案或加密监测')
      throw new EffectInputError(`指令台账里没有工作面 ${opts.panel} 已下发的封闭方案、防灭火方案或加密监测，请问指挥人员处置是什么时候开始的（矿井时间），作为 since 重新调用`)
    }
    since = last.issued_at
    treated = last.tool !== 'start_monitoring'
    sinceSource = treated
      ? `指令台账：${last.action_id ?? ''} ${NAMES[last.tool ?? ''] ?? last.tool} ${last.plan_id ?? ''} 的下发时间`.replace(/\s+/g, ' ')
      : `未采取处置，从指令台账 ${last.action_id ?? ''} 加密监测的开始时间起观察`.replace(/\s+/g, ' ')
    emit('since', 'completed', [], sinceSource)
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
  const evidence = await step('evidence', () => ev.collectEvidence({ skill: ER, kind: 'effect', panel: opts.panel, origin, since: since! }))
  const effect = await step('effect', () =>
    ev
      .runScript({ skill: ER, script: 'effect', inputs: { evidence: evidence.record_id }, options: { since_source: sinceSource, treated }, origin, ...(opts.signal ? { signal: opts.signal } : {}) })
      .then((x) => x.record),
  )
  const report = await step('report', () => ev.renderReport({ skill: ER, template: 'report', inputs: { effect: effect.record_id }, origin }))
  const brief = await step('brief', () =>
    ev.renderReport({ skill: ER, template: 'brief', inputs: { effect: effect.record_id }, origin }),
  )
  const r = (effect.content as any).result
  const warnings = ['判据为开发占位值，待专家审定', ...((effect.content as any).warnings ?? [])]
  return {
    card: {
      title: `${opts.panel} 工作面处置效果复评`,
      summary: `${r.overall_name}；${r.since_label ?? '处置开始'} ${r.since}（${r.since_source}）；已观测 ${r.observed_h} 小时；各结论测点数：${r.counts_text}${r.sealed_off_text ? `；封闭断电不参与：${r.sealed_off_text}` : ''}；下次复评 ${r.next_review_at}`,
      record_ids: [report.record_id, effect.record_id],
      files: [],
      next_actions: r.escalation_suggested ? [{ label: '重新研判（run_fire_assessment），判断是否升级', tool: 'run_fire_assessment', arguments: { panel: opts.panel } }] : [],
      warnings,
    },
    report_markdown: (report.content as string).trimEnd(),
    brief_markdown: (brief.content as string).trimEnd(),
    record_ids: ids,
    result: {
      panel: opts.panel,
      as_of: r.as_of,
      since: r.since,
      since_source: r.since_source,
      treated: r.treated !== false,
      overall: r.overall,
      overall_name: r.overall_name,
      observed_h: r.observed_h,
      counts: r.counts,
      next_review_at: r.next_review_at,
      escalation_suggested: r.escalation_suggested,
    },
    events,
  }
}
