/**
 * 固定流程 draft_sealing_plan（design-v2 4.4）：步骤由代码决定，模型只在两个阶段里做选择；判据和算法全在技能里。
 *
 *   采集证据（sealing-plan 的 plan 清单）
 *   → explosion_triangle（explosion-triangle）、warning、personnel（fire-assessment，总是计算受威胁人员）
 *   → skeleton（sealing-plan：现场条件、必备步骤、选择清单）
 *   → 每一轮：阶段“方案编写” → compose（组装方案、材料）→ 红线预检（actions 的 precheck_plan）
 *             → scope（plan-compliance：对照条文、审核项）→ 条文索引取原文 → 方案文本
 *             → 阶段“合规审核” → review（plan-compliance：结论）
 *     结论不通过时带着意见回到“方案编写”，最多 maxRounds 轮（占位 2，第五稿审定第 3 条）
 *   → 方案文本 + 审核意见；审核通过时返回审核记录编号，主预设用 issue_sealing_plan(record_id) 提交
 *
 * 红线预检不通过、且有缺失的人工确认项时，返回结构化的问题（HumanInputRequest），主预设 ask_user 后带着回答重新运行。
 * 起子 Agent、调 actions 的部分由 index.ts 注入（StageRunner、precheck），这里不依赖 DSH。
 */

import { randomInt } from 'node:crypto'

import type { Origin, RescueRecord } from '../evidence/records.ts'
import type { Evidence } from '../evidence/service.ts'
import type { FlowEvent } from './assessment.ts'
import type { StageRunner } from './explanation.ts'
import {
  checkReview,
  checkSelection,
  defaultSelection,
  REVIEW_STAGE,
  reviewPrompt,
  reviewSchema,
  type Scope,
  type Selection,
  type Skeleton,
  skeletonText,
  WRITE_STAGE,
  writePrompt,
  writeSchema,
  writeWithRetry,
} from './plan-stages.ts'

export const SEALING_FLOW = 'draft_sealing_plan'
export const FIREFIGHTING_FLOW = 'draft_firefighting_plan'
const PC = 'plan-compliance'
const FA = 'fire-assessment'

/**
 * 方案流程的配置：封闭方案和防灭火方案走同一套步骤（采集 → 骨架 → 方案编写 → 组装 → 红线预检 → 合规审核），
 * 区别只在技能、要不要算受威胁人员和人工确认项（防灭火方案不封闭火区、不安排人员进入灾区，两者都没有）、下发工具。
 */
export interface PlanFlow {
  name: string
  skill: string
  idPrefix: string
  /** 报告和卡片里的方案名 */
  label: string
  /** 下发用的 guard 工具（precheck_plan 的 tool 参数也取它） */
  issueTool: string
  /** 是否算预警等级、受威胁人员（撤人巷道）并接受人工确认项 */
  withPersonnel: boolean
}

export const SEALING: PlanFlow = { name: SEALING_FLOW, skill: 'sealing-plan', idPrefix: 'SP', label: '封闭方案', issueTool: 'issue_sealing_plan', withPersonnel: true }
export const FIREFIGHTING: PlanFlow = { name: FIREFIGHTING_FLOW, skill: 'firefighting-plan', idPrefix: 'FP', label: '防灭火方案', issueTool: 'issue_firefighting_plan', withPersonnel: false }

export interface HumanInput {
  field: string
  value: number | boolean
  confirmed_by: string
  note?: string | null
}

export interface Redline {
  ok: boolean
  problems: string[]
  notes: string[]
}

export interface SealingOptions {
  panel: string
  /** 现场条件的人工输入：只来自现场条件卡片（index.ts 的 resolveFieldChoices），不收模型直接填的值 */
  humanInputs?: HumanInput[]
  // 漏风量、目标氧浓度（注氮流量的输入）暂不收：没有卡片，方案里列为待补充（2026-11-11 代码复查第 2 轮）
  origin: Origin
  signal?: AbortSignal
  onEvent?: (e: FlowEvent) => void
  /** 跑模型阶段的子 Agent；不给时两个阶段都判失败（方案用缺省选择，审核没有结论，不能下发） */
  stage?: StageRunner
  /** 红线预检（actions 的 precheck_plan，经 DSH 工具管道） */
  precheck: (plan: Record<string, unknown>) => Promise<Redline>
  getArticles: (document: string, articles: string[]) => { document: string; article: string; content: string | null; source: string }[]
  /** 方案编写与合规审核的轮数上限（占位 2） */
  maxRounds?: number
  /** 操作员选定的可选步骤 key（防灭火方案：从处置选项卡片来）：给了就以它为准，模型不能增减 */
  forcedOptionalSteps?: string[]
}

export interface HumanInputRequest {
  flow: string
  reason: string
  items: { field: string; label: string; description: string | null; value_type: string; options: string[]; allow_text: boolean; blocking: boolean }[]
  record_ids: string[]
}

export interface SealingOutcome {
  card: {
    title: string
    summary: string
    record_ids: string[]
    files: never[]
    next_actions: { label: string; tool: string | null; arguments: Record<string, unknown> }[]
    warnings: string[]
  }
  report_markdown: string
  /** 方案要点（短文）：主预设原样贴进回答；完整方案和审核意见作为文件交付 */
  brief_markdown: string
  /** 给主预设的下一步说明（不是报告的一部分，不用贴进回答） */
  next_step: string
  record_ids: Record<string, string>
  result: Record<string, unknown>
  human_input_request: HumanInputRequest | null
  events: FlowEvent[]
}

const content = (r: RescueRecord) => (r.content as any).result

export const draftSealingPlan = (ev: Evidence, opts: SealingOptions) => draftPlan(ev, opts, SEALING)
export const draftFirefightingPlan = (ev: Evidence, opts: SealingOptions) => draftPlan(ev, opts, FIREFIGHTING)

export async function draftPlan(ev: Evidence, opts: SealingOptions, flow: PlanFlow): Promise<SealingOutcome> {
  const SP = flow.skill
  const runId = `${flow.idPrefix}-${Date.now().toString(36)}-${randomInt(36 ** 4).toString(36).padStart(4, '0')}`
  const events: FlowEvent[] = []
  const emit = (stage: string, status: FlowEvent['status'], recordIds: string[] = [], detail: string | null = null) => {
    const e: FlowEvent = { flow: flow.name, run_id: runId, stage, status, at: new Date().toISOString(), record_ids: recordIds, detail }
    events.push(e)
    opts.onEvent?.(e)
  }
  const origin = opts.origin
  const sig = opts.signal ? { signal: opts.signal } : {}
  const ids: Record<string, string> = {}
  const maxRounds = opts.maxRounds ?? 2

  async function step(stage: string, fn: () => Promise<RescueRecord>, key = stage): Promise<RescueRecord> {
    emit(stage, 'started')
    try {
      const r = await fn()
      ids[key] = r.record_id
      emit(stage, 'completed', [r.record_id])
      return r
    } catch (err) {
      emit(stage, 'failed', [], (err as Error).message)
      throw err
    }
  }
  const script = (skill: string, name: string, inputs: Record<string, string>, options?: Record<string, unknown>) =>
    ev.runScript({ skill, script: name, inputs, origin, ...(options ? { options } : {}), ...sig }).then((x) => x.record)

  const evidence = await step('evidence', () => ev.collectEvidence({ skill: SP, kind: 'plan', panel: opts.panel, origin }))
  const E = evidence.record_id
  const explosion = await step('explosion', () => script('explosion-triangle', 'explosion_triangle', { evidence: E }))
  const skInputs: Record<string, string> = { evidence: E, explosion: explosion.record_id }
  const skOptions: Record<string, unknown> = {}
  let personnel: RescueRecord | null = null
  if (flow.withPersonnel) {
    await step('warning', () => script(FA, 'warning', { evidence: E }))
    personnel = await step('personnel', () => script(FA, 'personnel', { evidence: E, warning: ids.warning! }, { include_personnel: true }))
    skInputs.personnel = personnel.record_id
    skOptions.human_inputs = opts.humanInputs ?? []
  }
  const skeletonRec = await step('skeleton', () => script(SP, 'skeleton', skInputs, skOptions))
  const sk = content(skeletonRec) as Skeleton
  // 操作员选定的可选步骤（处置选项卡片）：必须都在清单里，按清单顺序；以它为准，模型不能增减
  let forced: string[] | undefined
  if (opts.forcedOptionalSteps) {
    const have = new Set(sk.optional_steps.map((x) => x.key))
    const bad = opts.forcedOptionalSteps.filter((k) => !have.has(k))
    if (bad.length) throw new Error(`操作员选定的步骤不在方案的可选步骤清单里：${bad.join('、')}`)
    forced = sk.optional_steps.map((x) => x.key).filter((k) => opts.forcedOptionalSteps!.includes(k))
  }
  const material0 = [skeletonText(sk), JSON.stringify(sk), JSON.stringify(content(explosion)), ...(personnel ? [JSON.stringify(content(personnel))] : [])].join('\n')

  const [spSkill, pcSkill] = await Promise.all([ev.skill(SP), ev.skill(PC)])
  const warnings: string[] = ['方案结构、材料参数和审核项为开发占位值，待专家审定']
  let comments: string[] = []
  let needWrite = true
  let planRec: RescueRecord | null = null
  let planReport: RescueRecord | null = null
  let scope: Scope | null = null
  let scopeRec: RescueRecord | null = null
  let redline: Redline = { ok: false, problems: [], notes: [] }
  let articles: { document: string; article: string; content: string | null; source: string }[] = []
  let reviewRec: RescueRecord | null = null
  let rounds = 0
  const stageNotes: string[] = []
  let lastSelection: Selection | null = null

  for (let round = 1; round <= maxRounds; round++) {
    rounds = round
    if (needWrite) {
      // ── 阶段“方案编写” ──
      emit(`${WRITE_STAGE}#${round}`, 'started')
      let selection: Selection | null = null
      let problem = '没有配置阶段子 Agent'
      if (opts.stage) {
        const stage = opts.stage
        ;({ selection, problem } = await writeWithRetry(
          (fix) =>
            stage({
              label: WRITE_STAGE,
              prompt: writePrompt({ skillName: spSkill.name, skillVersion: spSkill.version, skillBody: spSkill.body, skeleton: sk, comments: [...comments, ...fix], flow: flow.name, ...(forced ? { forcedOptionalSteps: forced } : {}) }),
              schema: writeSchema(sk),
              ...sig,
            }),
          (structured) => checkSelection(forced && structured && typeof structured === 'object' ? { ...(structured as object), optional_steps: forced } : structured, sk, material0 + '\n' + comments.join('\n')),
        ))
      }
      if (selection) emit(`${WRITE_STAGE}#${round}`, 'completed')
      else if (lastSelection) {
        // 重做也不合格：沿用上一轮的选择（不退回缺省，免得丢掉已选的可选步骤）
        emit(`${WRITE_STAGE}#${round}`, 'failed', [], problem)
        stageNotes.push(`第 ${round} 轮“${WRITE_STAGE}”未完成（${problem}），沿用上一轮的选择`)
        selection = lastSelection
      } else {
        emit(`${WRITE_STAGE}#${round}`, 'failed', [], problem)
        stageNotes.push(`第 ${round} 轮“${WRITE_STAGE}”未完成（${problem}），按缺省选择组装（${sk.seal_roadway_options.length ? '封闭全部候选巷道、' : ''}不加可选步骤）`)
        selection = defaultSelection(sk)
        if (forced) selection = { ...selection, optional_steps: forced }
      }
      lastSelection = selection
      planRec = await step(`compose#${round}`, () => script(SP, 'compose', { skeleton: skeletonRec.record_id, evidence: E }, { selection }), 'compose')
      const plan = content(planRec).plan as Record<string, unknown>
      emit(`redline#${round}`, 'started')
      try {
        redline = await opts.precheck(plan)
        emit(`redline#${round}`, 'completed', [], redline.ok ? '通过' : redline.problems.join('；'))
      } catch (err) {
        emit(`redline#${round}`, 'failed', [], (err as Error).message)
        throw new Error(`红线预检失败（actions 的 precheck_plan）：${(err as Error).message}`)
      }
      scopeRec = await step(`scope#${round}`, () => script(PC, 'scope', { plan: planRec!.record_id }), 'scope')
      scope = content(scopeRec) as Scope
      const refs = scope.articles
      const docs = [...new Set(refs.map((a) => a.document))]
      articles = docs.flatMap((d) => opts.getArticles(d, refs.filter((a) => a.document === d).map((a) => a.article)))
      const basis = (plan.basis as { document: string; article: string }[]).map((b) => articles.find((a) => a.document === b.document && a.article === b.article) ?? { ...b, content: null, source: 'not-found' })
      planReport = await step(`plan_report#${round}`, () => ev.renderReport({ skill: SP, template: 'plan', inputs: { compose: planRec!.record_id }, extra: { articles: basis }, origin }), 'plan_report')
    }

    // ── 阶段“合规审核” ──
    emit(`${REVIEW_STAGE}#${round}`, 'started')
    let choice = null
    let problem = '没有配置阶段子 Agent'
    if (opts.stage) {
      const planText = planReport!.content as string
      const run = await opts.stage({
        label: REVIEW_STAGE,
        prompt: reviewPrompt({ skillName: pcSkill.name, skillVersion: pcSkill.version, skillBody: pcSkill.body, scope: scope!, planText, redline, articles, flow: flow.name }),
        schema: reviewSchema(scope!),
        ...sig,
      })
      const material = [planText, JSON.stringify(content(planRec!)), redline.problems.join('\n'), ...articles.map((a) => a.content ?? '')].join('\n')
      const checked = run.ok ? checkReview(run.structured, scope!, material) : { ok: false as const, problems: [run.error] }
      if (checked.ok) choice = checked.review
      else problem = checked.problems.join('；')
    }
    if (!choice) {
      emit(`${REVIEW_STAGE}#${round}`, 'failed', [], problem)
      stageNotes.push(`第 ${round} 轮“${REVIEW_STAGE}”未完成（${problem}）`)
      needWrite = false // 方案没变，下一轮只重做审核
      continue
    }
    emit(`${REVIEW_STAGE}#${round}`, 'completed')
    reviewRec = await step(
      `review#${round}`,
      () => script(PC, 'review', { plan: planRec!.record_id, scope: scopeRec!.record_id }, { redline, review: choice, round }),
      'review',
    )
    const rv = content(reviewRec)
    if (rv.verdict === 'pass') break
    comments = [...redline.problems.map((p) => `红线预检：${p}`), ...rv.failed_items.map((i: any) => `${i.label}：${i.note || '不通过'}`)]
    needWrite = true
  }

  // ── 结果 ──
  const rv = reviewRec ? content(reviewRec) : null
  const passed = rv?.verdict === 'pass'
  let reviewReport: RescueRecord | null = null
  if (reviewRec) {
    const rid = reviewRec.record_id
    // 方案依据的条文原文已在方案文本里，审核意见只列其余对照条文的原文（报告短一些，主预设照抄更可靠）
    const basis = new Set(((content(planRec!).plan.basis ?? []) as { document: string; article: string }[]).map((b) => `${b.document}${b.article}`))
    const others = articles.filter((a) => !basis.has(`${a.document}${a.article}`))
    reviewReport = await step('review_report', () => ev.renderReport({ skill: PC, template: 'review', inputs: { review: rid }, extra: { articles: others }, origin }))
  }
  // 红线不通过、又有缺失的人工确认项：先问人
  const missingHuman = sk.missing_inputs.filter((m) => m.kind === 'human')
  const humanRequest: HumanInputRequest | null =
    !redline.ok && missingHuman.length
      ? {
          flow: flow.name,
          reason: `红线预检不通过，可能与缺失的现场条件有关：${redline.problems.join('；')}`,
          items: missingHuman.map((m) => ({
            field: m.field,
            label: m.label,
            description: m.note,
            value_type: m.value_type,
            options: m.value_type === 'bool' ? ['是', '否'] : [],
            allow_text: true,
            blocking: true,
          })),
          record_ids: [skeletonRec.record_id],
        }
      : null

  const p = content(planRec!)
  const verdictText = passed ? '合规审核通过' : rv ? '合规审核不通过' : '合规审核未完成'
  const summary = [
    `${p.plan.title}（${p.plan.plan_id}）`,
    `${p.step_count} 个步骤`,
    ...(p.seal_walls_text ? [`密闭巷道 ${p.seal_walls_text}`] : []),
    `红线预检${redline.ok ? '通过' : '不通过'}`,
    `${verdictText}（${rounds} 轮）`,
    p.has_shortages ? `有缺口的物资 ${p.shortage_count} 项` : '物资台账无缺口',
  ].join('；')
  warnings.push(...stageNotes, ...((content(skeletonRec) as any).data_quality ?? []))
  const missingArticles = articles.filter((a) => a.source !== 'index')
  if (missingArticles.length) warnings.push(`条文索引里没有：${missingArticles.map((a) => `${a.document}${a.article}`).join('、')}`)
  const steps = (p.steps_view as { order: number; type_name: string }[]).map((x) => `${x.order}. ${x.type_name}`).join('；')
  const humanCount = (rv?.human_items ?? []).length
  const brief = await step('brief', () =>
    ev.renderReport({
      skill: SP,
      template: 'brief',
      inputs: { compose: planRec!.record_id },
      extra: {
        steps_text: steps,
        redline_text: redline.ok ? '通过' : `不通过（${redline.problems.join('；')}）`,
        verdict_text: `${verdictText}（${rounds} 轮）`,
        human_count: humanCount || null,
        review_id: reviewRec?.record_id ?? null,
      },
      origin,
    }),
  )
  const facts = [(planReport!.content as string).trimEnd(), reviewReport ? (reviewReport.content as string).trimEnd() : `【合规审核】未完成：${stageNotes.join('；')}`]
  const dispatchNote = p.has_dispatch ? `方案下发后，再用 issue_dispatch_order 提交调度单（record_id 填同一个审核记录 ${reviewRec?.record_id ?? ''}，编号、用途、明细留空，由系统按方案的需调运 ${p.dispatch_count} 项填写）。` : ''
  const tail = passed
    ? `下发：指挥人员确认后用 ${flow.issueTool} 提交审核记录 ${reviewRec!.record_id}（会弹出人工确认）。${dispatchNote}`
    : humanRequest
      ? '下发前需要先补充现场确认项（见问题清单），带着回答重新编制。'
      : `方案未通过合规审核，不能下发；请指挥人员决定如何处理。`
  return {
    card: {
      title: `${opts.panel} ${flow.label}`,
      summary,
      record_ids: [planReport!.record_id, ...(reviewReport ? [reviewReport.record_id] : []), ...(reviewRec ? [reviewRec.record_id] : []), planRec!.record_id],
      files: [],
      next_actions: passed
        ? [
            { label: `下发${flow.label}（${flow.issueTool}，需人工确认）`, tool: flow.issueTool, arguments: { record_id: reviewRec!.record_id } },
            ...(p.has_dispatch ? [{ label: `下发物资调度单（需调运 ${p.dispatch_count} 项，需人工确认）`, tool: 'issue_dispatch_order', arguments: { order: { record_id: reviewRec!.record_id } } }] : []),
          ]
        : [],
      warnings,
    },
    report_markdown: facts.join('\n\n'),
    brief_markdown: (brief.content as string).trimEnd(),
    next_step: tail,
    record_ids: ids,
    result: {
      panel: opts.panel,
      as_of: evidence.as_of,
      plan_id: p.plan.plan_id,
      verdict: passed ? 'pass' : rv ? 'fail' : 'incomplete',
      verdict_name: verdictText,
      rounds,
      redline_ok: redline.ok,
      redline_problems: redline.problems,
      issue_record_id: passed ? reviewRec!.record_id : null,
      step_count: p.step_count,
      shortage_count: p.shortage_count,
      dispatch_count: p.dispatch_count ?? 0,
      human_items: rv?.human_items ?? [],
    },
    human_input_request: humanRequest,
    events,
  }
}
