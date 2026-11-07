/**
 * 固定流程 run_fire_assessment（design-v2 4.4）：步骤由代码决定，模型不能改；判据全在技能脚本里。
 *
 *   采集证据（fire-assessment 的 assessment 清单）
 *   → alarms、warning → stage → trend → explosion_triangle（逐个束管测点）→ personnel（脚本按等级决定是否计算）
 *   → escalation（emergency-escalation）
 *   → 研判报告（fire-assessment/templates/report.md，含风险图）+ 升级建议（emergency-escalation/templates/report.md）
 *
 *   → explanation_options（选择清单）→ 阶段“研判说明”（模型只从清单里选关注点和下一步，代码核对；失败时报告只含事实部分）
 *
 * 每步发出流程事件（FlowEvent），随结果返回。
 */

import { randomInt } from 'node:crypto'

import type { Origin, RescueRecord } from '../evidence/records.ts'
import type { Evidence } from '../evidence/service.ts'
import { checkExplanation, type Options, STAGE, type StageRunner, stagePrompt, stageSchema } from './explanation.ts'

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
  /** 跑阶段“研判说明”的子 Agent；不给时跳过该阶段（报告只含事实部分） */
  stage?: StageRunner
  /** 按文档和条号取条文原文（知识库插件的条文索引）；升级建议里的条文用它 */
  getArticles: (document: string, articles: string[]) => { document: string; article: string; content: string | null; source: string }[]
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
  /** 研判要点（短文）：主预设原样贴进回答；完整报告作为文件交付 */
  brief_markdown: string
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
  // 升级建议引用的条文：按脚本给出的条号从条文索引取原文（不由模型、不由脚本写原文）
  const refs: { document: string; article: string }[] = content(rec.escalation!).articles
  const docs = [...new Set(refs.map((a) => a.document))]
  const articles = docs.flatMap((d) =>
    opts.getArticles(d, refs.filter((a) => a.document === d).map((a) => a.article)).map((a) => ({ document: a.document, article: a.article, content: a.content, source: a.source })),
  )
  const escReport = await step('escalation_report', () =>
    ev.renderReport({ skill: 'emergency-escalation', template: 'report', inputs: { escalation: ids.escalation! }, extra: { articles }, origin }),
  )

  // ── 阶段“研判说明” ──
  const facts = `${(report.content as string).trimEnd()}\n\n${(escReport.content as string).trimEnd()}`
  const options = await step('explanation_options', () =>
    script(FA, 'explanation_options', Object.fromEntries(['warning', 'stage', 'alarms', 'trend', 'explosion', 'personnel', 'escalation'].map((k) => [k, ids[k]!]))),
  )
  let explanationMd: string | null = null
  let explanationSummary: string | null = null
  let explanationNote: string
  if (!opts.stage) {
    emit('explanation', 'skipped', [], '没有配置阶段子 Agent')
    explanationNote = '研判说明未生成（没有配置阶段子 Agent）'
  } else {
    emit('explanation', 'started')
    const o = content(options) as Options
    const skill = await ev.skill(FA)
    const material = [facts, JSON.stringify(o), ...['warning', 'stage', 'trend', 'explosion', 'personnel', 'escalation'].map((k) => JSON.stringify(content(rec[k]!)))].join('\n')
    const run = await opts.stage({
      label: STAGE,
      prompt: stagePrompt({ skillName: skill.name, skillVersion: skill.version, skillBody: skill.body, facts, options: o }),
      schema: stageSchema(o),
      ...sig,
    })
    const checked = run.ok ? checkExplanation(run.structured, o, material) : { ok: false as const, problems: [run.error] }
    if (checked.ok) {
      const r = await ev.renderReport({ skill: FA, template: 'explanation', inputs: { options: options.record_id }, extra: { explanation: checked.explanation, child_id: run.childId }, origin })
      ids.explanation = r.record_id
      rec.explanation = r
      explanationSummary = checked.explanation.summary
      explanationMd = (r.content as string).trimEnd()
      explanationNote = ''
      emit('explanation', 'completed', [r.record_id])
    } else {
      explanationNote = `研判说明未生成（${checked.problems.join('；')}）`
      emit('explanation', 'failed', [], checked.problems.join('；'))
    }
  }

  // 研判要点：主预设原样贴进回答的短文；完整报告作为文件交付（网页端右侧预览）
  const worstPoint = x.points.find((p: any) => p.sensor_id === x.worst_sensor_id)
  const brief = await step('brief', () =>
    ev.renderReport({
      skill: FA,
      template: 'brief',
      inputs: Object.fromEntries(['warning', 'stage', 'alarms', 'explosion', 'personnel', 'escalation'].map((k) => [k, ids[k]!])),
      extra: {
        worst: worstPoint ? { sensor_id: worstPoint.sensor_id, zone: worstPoint.zone, zone_name: worstPoint.zone_name } : null,
        articles_text: refs.map((a) => `${a.document}${a.article}`).join('、') || '无',
        explanation_summary: explanationSummary,
        chart_markdown: chartMarkdown,
      },
      origin,
    }),
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
  if (explanationNote) warnings.push(explanationNote)
  const missingArticles = articles.filter((a) => a.source !== 'index')
  if (missingArticles.length) warnings.push(`条文索引里没有：${missingArticles.map((a) => `${a.document}${a.article}`).join('、')}`)
  const nextActions = [
    ...(es.recommend ? [{ label: '升级应急响应（escalate_emergency，需人工确认）', tool: null, arguments: {} }] : []),
    { label: '发布预警（publish_warning，需人工确认）', tool: null, arguments: {} },
  ]
  return {
    card: {
      title: `${opts.panel} 工作面研判`,
      summary,
      record_ids: [report.record_id, escReport.record_id, ...(ids.explanation ? [ids.explanation] : []), ids.warning!, ids.explosion!, ids.escalation!],
      files: rec.explosion!.files,
      next_actions: nextActions,
      warnings,
    },
    report_markdown: `${facts}\n\n${explanationMd ?? `【研判说明】${explanationNote}`}`,
    brief_markdown: (brief.content as string).trimEnd(),
    record_ids: ids,
    result: {
      panel: opts.panel,
      as_of: evidence.as_of,
      warning: { level: w.level, level_name: w.level_name, stage: st.stage, stage_name: st.stage_name },
      alarms: al.alarms,
      reseal_indicated: al.reseal_indicated,
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
      explanation: explanationMd ? { generated: true, record_id: ids.explanation } : { generated: false, note: explanationNote },
    },
    events,
  }
}
