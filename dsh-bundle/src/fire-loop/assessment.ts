/**
 * 固定流程 run_fire_assessment（design-v2 4.4）：步骤由代码决定，模型不能改；判据全在技能脚本里。
 *
 *   采集证据（fire-assessment 的 assessment 清单）
 *   → alarms、warning → stage → trend → explosion_triangle（逐个束管测点）→ personnel（脚本按等级决定是否计算）
 *   → escalation（emergency-escalation）
 *   → 研判报告（fire-assessment/templates/report.md，含风险图）+ 升级建议（emergency-escalation/templates/report.md）
 *
 * 每步发出流程事件（FlowEvent），随结果返回。阶段“研判说明”（模型只从给定清单里选关注点和下一步）以后加；
 * 现在由主预设在报告后面写“分析与建议”。
 */

import { randomInt } from 'node:crypto'

import type { Origin, RescueRecord } from '../evidence/records.ts'
import type { Evidence } from '../evidence/service.ts'

export const FLOW = 'run_fire_assessment'
const FA = 'fire-assessment'

export interface FlowEvent {
  flow: string
  run_id: string
  stage: string
  status: 'started' | 'completed' | 'failed' | 'skipped'
  at: string
  record_ids: string[]
  detail: string | null
}

export interface AssessmentOptions {
  panel: string
  includePersonnel?: boolean
  origin: Origin
  signal?: AbortSignal
  /** 把记录附带的风险图放到网页端能显示的地方，返回路径；不给时报告里不放图 */
  chartPath?: (recordId: string) => Promise<string | null>
  onEvent?: (e: FlowEvent) => void
}

export interface AssessmentOutcome {
  card: {
    title: string
    summary: string
    record_ids: string[]
    files: { name: string; media_type: string; size: number; sha256: string }[]
    next_actions: { label: string; tool: string | null; arguments: Record<string, unknown> }[]
    warnings: string[]
  }
  report_markdown: string
  record_ids: Record<string, string>
  result: Record<string, unknown>
  events: FlowEvent[]
}

const content = (r: RescueRecord) => (r.content as any).result

export async function runFireAssessment(ev: Evidence, opts: AssessmentOptions): Promise<AssessmentOutcome> {
  const runId = `FA-${Date.now().toString(36)}-${randomInt(36 ** 4).toString(36).padStart(4, '0')}`
  const events: FlowEvent[] = []
  const emit = (stage: string, status: FlowEvent['status'], recordIds: string[] = [], detail: string | null = null) => {
    const e: FlowEvent = { flow: FLOW, run_id: runId, stage, status, at: new Date().toISOString(), record_ids: recordIds, detail }
    events.push(e)
    opts.onEvent?.(e)
  }
  const origin = opts.origin
  const sig = opts.signal ? { signal: opts.signal } : {}
  const ids: Record<string, string> = {}
  const rec: Record<string, RescueRecord> = {}

  async function step(stage: string, fn: () => Promise<RescueRecord>): Promise<RescueRecord> {
    emit(stage, 'started')
    try {
      const r = await fn()
      ids[stage] = r.record_id
      rec[stage] = r
      emit(stage, 'completed', [r.record_id])
      return r
    } catch (err) {
      emit(stage, 'failed', [], (err as Error).message)
      throw err
    }
  }
  const script = (skill: string, name: string, inputs: Record<string, string>, options?: Record<string, unknown>) =>
    ev.runScript({ skill, script: name, inputs, origin, ...(options ? { options } : {}), ...sig }).then((x) => x.record)

  const evidence = await step('evidence', () => ev.collectEvidence({ skill: FA, kind: 'assessment', panel: opts.panel, origin }))
  const E = evidence.record_id
  await step('alarms', () => script(FA, 'alarms', { evidence: E }))
  await step('warning', () => script(FA, 'warning', { evidence: E }))
  await step('stage', () => script(FA, 'stage', { warning: ids.warning! }))
  await step('trend', () => script(FA, 'trend', { evidence: E, warning: ids.warning! }))
  await step('explosion', () => script('explosion-triangle', 'explosion_triangle', { evidence: E }))
  await step('personnel', () => script(FA, 'personnel', { evidence: E, warning: ids.warning! }, { include_personnel: Boolean(opts.includePersonnel) }))
  await step('escalation', () => script('emergency-escalation', 'escalation', { warning: ids.warning!, stage: ids.stage!, alarms: ids.alarms! }))

  const x = content(rec.explosion!)
  let chartMarkdown: string | null = null
  if (opts.chartPath && rec.explosion!.files.length) {
    const path = await opts.chartPath(ids.explosion!)
    const who = x.chart_sensor_id ? `（最不利测点 ${x.chart_sensor_id}）` : ''
    if (path) chartMarkdown = `![火区混合气体爆炸三角形${who}](${path})`
  }
  const report = await step('report', () =>
    ev.renderReport({
      skill: FA,
      template: 'report',
      inputs: Object.fromEntries(['evidence', 'warning', 'stage', 'alarms', 'trend', 'explosion', 'personnel'].map((k) => [k, ids[k]!])),
      extra: { chart_markdown: chartMarkdown },
      origin,
    }),
  )
  const escReport = await step('escalation_report', () =>
    ev.renderReport({ skill: 'emergency-escalation', template: 'report', inputs: { escalation: ids.escalation! }, origin }),
  )

  const w = content(rec.warning!)
  const st = content(rec.stage!)
  const al = content(rec.alarms!)
  const pe = content(rec.personnel!)
  const es = content(rec.escalation!)
  const summary = [
    `预警等级 ${w.level_name}`,
    `自燃发展阶段 ${st.stage_name}`,
    `爆炸危险性 ${x.overall_name}`,
    `实时报警 ${al.count} 条`,
    pe.computed ? `受威胁 ${pe.total_threatened} 人` : pe.note,
    es.recommend ? '建议升级为应急响应' : '暂不建议升级为应急响应',
  ].join('；')
  const warnings = ['阈值和判据为开发占位值，待专家审定']
  if (x.overall === 'unknown') warnings.push('有束管测点算不出爆炸三角形，爆炸危险性按“无法判定”对待')
  if (w.notes.length) warnings.push(...w.notes)
  const nextActions = [
    ...(es.recommend ? [{ label: '升级应急响应（escalate_emergency，需人工确认）', tool: null, arguments: {} }] : []),
    { label: '发布预警（publish_warning，需人工确认）', tool: null, arguments: {} },
  ]
  return {
    card: {
      title: `${opts.panel} 工作面研判`,
      summary,
      record_ids: [report.record_id, escReport.record_id, ids.warning!, ids.explosion!, ids.escalation!],
      files: rec.explosion!.files,
      next_actions: nextActions,
      warnings,
    },
    report_markdown: `${(report.content as string).trimEnd()}\n\n${(escReport.content as string).trimEnd()}`,
    record_ids: ids,
    result: {
      panel: opts.panel,
      as_of: evidence.as_of,
      warning: { level: w.level, level_name: w.level_name, stage: st.stage, stage_name: st.stage_name },
      alarms: al.alarms,
      explosion: {
        overall: x.overall,
        overall_name: x.overall_name,
        worst_sensor_id: x.worst_sensor_id,
        points: x.points.map((p: any) => ({ sensor_id: p.sensor_id, zone: p.zone, zone_name: p.zone_name, explosion_risk: p.explosion_risk })),
        failures: x.failures,
        chart_markdown: chartMarkdown,
      },
      personnel: pe.computed ? { computed: true, total_threatened: pe.total_threatened, total_underground: pe.total_underground } : { computed: false, note: pe.note },
      escalation: { recommend: es.recommend, articles: es.articles.map((a: any) => ({ document: a.document, article: a.article })) },
    },
    events,
  }
}
