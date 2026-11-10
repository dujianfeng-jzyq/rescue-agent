/**
 * 固定流程 draft_sealing_plan、draft_firefighting_plan 的两个模型阶段（design-v2 4.4 第 3 点）：“方案编写”“合规审核”。
 *
 * 模型只做选择：编号（可选步骤、密闭巷道、审核项、结论、条号）来自代码给出的清单，写短说明。
 * 阶段指令 = 相应技能的 SKILL.md 正文（sealing-plan、plan-compliance，每次运行读当前版本）+ 本阶段说明。
 * 代码检查：编号来自清单、不重复、字数不超限、说明文字里的数值都能在本次材料里找到；不合格就判阶段失败。
 * 选择的合法性由技能脚本（compose.py、review.py）再核对一遍。这里只放纯逻辑。
 */

import { ungrounded } from './explanation.ts'

export const WRITE_STAGE = '方案编写'
export const REVIEW_STAGE = '合规审核'

export interface Skeleton {
  panel: string
  /** sealing（封闭方案）/ firefighting（防灭火方案，不封闭火区、不进入灾区，没有密闭巷道、撤人和人工确认项） */
  kind: string
  as_of: string
  title: string
  context: Record<string, unknown>
  explosion: { overall_name: string | null; worst_sensor_id: string | null }
  required_steps: { key: string; type: string; description: string }[]
  optional_steps: { key: string; type: string; label: string; after: string; description: string }[]
  seal_roadway_options: { roadway_id: string; name: string; kind: string }[]
  evacuate_roadways: string[]
  missing_inputs: { field: string; label: string; value_type: string; kind: string; note: string }[]
  human_inputs: { field: string; label: string; value: unknown; confirmed_by: string }[]
  limits: { max_note_chars: number; max_summary_chars: number }
}

export interface Selection {
  optional_steps: string[]
  seal_roadways: string[]
  step_notes: { key: string; note: string }[]
  summary: string
}

export interface Scope {
  articles: { document: string; article: string }[]
  items: { id: string; label: string }[]
  conclusions: { id: string; label: string }[]
  limits: { max_note_chars: number; max_summary_chars: number }
}

export interface ReviewChoice {
  items: { id: string; conclusion: string; articles: string[]; note: string }[]
  summary: string
}

const ROLE = (stage: string, flow: string) =>
  `你是固定流程 ${flow} 的“${stage}”阶段。只用 structured_output 提交结果，不要调用任何其他工具，不要向人提问。`
const NUMBERS =
  '说明、概述里不写清单、现场条件和条文里没有的数值：不换算、不估算、不相加、不计数、不归纳界限；不写序号；不声称已执行任何处置。写不出合规的说明时留空字符串。'

const show = (v: unknown) => (v === null || v === undefined ? '缺失' : v === true ? '是' : v === false ? '否' : String(v))

/** 默认选择：阶段失败时用（封闭全部候选巷道——没有密闭巷道的方案就没有——不加可选步骤），报告注明 */
export function defaultSelection(sk: Skeleton): Selection {
  return { optional_steps: [], seal_roadways: sk.seal_roadway_options.map((o) => o.roadway_id), step_notes: [], summary: '' }
}

export function writeSchema(sk: Skeleton): Record<string, unknown> {
  const stepKeys = [...sk.required_steps.map((s) => s.key), ...sk.optional_steps.map((s) => s.key)]
  return {
    type: 'object',
    additionalProperties: false,
    properties: {
      optional_steps: { type: 'array', items: { type: 'string', enum: sk.optional_steps.map((s) => s.key) } },
      // DSH 的结构化输出只支持 JSON Schema 的一个子集（没有 minItems），“至少一条”由 checkSelection 检查
      ...(sk.seal_roadway_options.length ? { seal_roadways: { type: 'array', items: { type: 'string', enum: sk.seal_roadway_options.map((o) => o.roadway_id) } } } : {}),
      step_notes: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          properties: { key: { type: 'string', enum: stepKeys }, note: { type: 'string' } },
          required: ['key', 'note'],
        },
      },
      summary: { type: 'string' },
    },
    required: ['optional_steps', ...(sk.seal_roadway_options.length ? ['seal_roadways'] : []), 'step_notes', 'summary'],
  }
}

/** 骨架的文字版（阶段的输入，也是数值核对的材料之一） */
export function skeletonText(sk: Skeleton): string {
  const c = sk.context
  if (sk.kind === 'firefighting') {
    return [
      `## 现场条件（截至 ${sk.as_of}，代码生成，不能改）`,
      '',
      `- 灾区甲烷 ${show(c.methane_pct)} %，是否继续上升：${show(c.methane_rising)}`,
      `- 氧浓度 ${show(c.o2_pct)} %${sk.explosion.worst_sensor_id ? `（${sk.explosion.worst_sensor_id}）` : ''}`,
      `- 爆炸危险性：${show(sk.explosion.overall_name)}`,
      '- 本方案不封闭火区，不安排人员进入灾区，没有密闭巷道和撤人步骤',
      '',
      '## 必备步骤（顺序和说明固定）',
      '',
      ...sk.required_steps.map((s) => `- ${s.key}：${s.description}`),
      '',
      '## 可选步骤清单（key：内容，插在哪一步之后）',
      '',
      ...sk.optional_steps.map((s) => `- ${s.key}：${s.label}（在 ${s.after} 之后）`),
      '',
      '## 待补充（这一阶段改不了）',
      '',
      ...(sk.missing_inputs.length ? sk.missing_inputs.map((m) => `- ${m.label}：${m.note}`) : ['- 无']),
    ].join('\n')
  }
  return [
    `## 现场条件（截至 ${sk.as_of}，代码生成，不能改）`,
    '',
    `- 灾区甲烷 ${show(c.methane_pct)} %，是否继续上升：${show(c.methane_rising)}`,
    `- 氧浓度 ${show(c.o2_pct)} %${sk.explosion.worst_sensor_id ? `（${sk.explosion.worst_sensor_id}）` : ''}`,
    `- 爆炸危险性：${show(sk.explosion.overall_name)}`,
    `- 火区封闭时间：${show(c.sealed_at)}`,
    `- 灾区温度 ${show(c.area_temperature_c)} ℃，能见度 ${show(c.visibility_m)} 米，火源是否明确 ${show(c.fire_source_confirmed)}，水源人力物力是否充足 ${show(c.water_supply_sufficient)}，回风道是否畅通 ${show(c.return_airway_clear)}`,
    ...sk.human_inputs.map((h) => `- 人工确认：${h.label} ${show(h.value)}（确认人 ${h.confirmed_by}）`),
    '',
    '## 必备步骤（顺序和说明固定）',
    '',
    ...sk.required_steps.map((s) => `- ${s.key}：${s.description}`),
    `- 撤人巷道（受威胁人员所在）：${sk.evacuate_roadways.join('、') || '未算出，按预案确定'}`,
    '',
    '## 可选步骤清单（key：内容，插在哪一步之后）',
    '',
    ...sk.optional_steps.map((s) => `- ${s.key}：${s.label}（在 ${s.after} 之后）`),
    '',
    '## 密闭巷道清单',
    '',
    ...sk.seal_roadway_options.map((o) => `- ${o.roadway_id}：${o.name}（${o.kind === 'intake' ? '进风侧' : o.kind === 'return' ? '回风侧' : o.kind}）`),
    '',
    '## 待补充（这一阶段改不了）',
    '',
    ...(sk.missing_inputs.length ? sk.missing_inputs.map((m) => `- ${m.label}：${m.note}`) : ['- 无']),
  ].join('\n')
}

export function writePrompt(opts: { skillName: string; skillVersion: string; skillBody: string; skeleton: Skeleton; comments: string[]; flow?: string; forcedOptionalSteps?: string[] }): string {
  const l = opts.skeleton.limits
  return [
    ROLE(WRITE_STAGE, opts.flow ?? 'draft_sealing_plan'),
    '',
    '本阶段要求：',
    opts.forcedOptionalSteps
      ? `- optional_steps：操作员已选定，必须正好是 [${opts.forcedOptionalSteps.join('、')}]（不得增减；没有选就是空）；只给这些步骤写补充说明；`
      : '- optional_steps：从“可选步骤清单”里选需要的（可以不选）；',
    ...(opts.skeleton.seal_roadway_options.length ? ['- seal_roadways：从“密闭巷道清单”里选要封闭的巷道（至少一条）；'] : []),
    `- step_notes：给需要补充执行要点的步骤各写一句（key 用必备步骤或所选可选步骤的 key，不超过 ${l.max_note_chars} 字），可以不写；`,
    `- summary：一句方案概述（不超过 ${l.max_summary_chars} 字）。`,
    `- ${NUMBERS}`,
    '',
    `以下是技能 ${opts.skillName}（${opts.skillVersion}）的说明：`,
    '',
    opts.skillBody.trim(),
    '',
    `# 本次方案骨架：${opts.skeleton.title}`,
    '',
    skeletonText(opts.skeleton),
    ...(opts.comments.length ? ['', '## 上一轮合规审核的意见（按意见调整选择和补充说明）', '', ...opts.comments.map((x) => `- ${x}`)] : []),
  ].join('\n')
}

function noteProblems(text: string, max: number, what: string, material: string): string[] {
  const out: string[] = []
  if (text.length > max) out.push(`${what}超过 ${max} 字`)
  const bad = ungrounded(text, material)
  if (bad.length) out.push(`${what}里有记录中找不到的数值：${bad.join('、')}`)
  return out
}

/** 核对“方案编写”的结构化结果 */
export function checkSelection(raw: unknown, sk: Skeleton, material: string): { ok: true; selection: Selection } | { ok: false; problems: string[] } {
  const r = raw as any
  const hasSeals = sk.seal_roadway_options.length > 0
  if (!r || typeof r !== 'object' || !Array.isArray(r.optional_steps) || (hasSeals && !Array.isArray(r.seal_roadways)) || !Array.isArray(r.step_notes) || typeof r.summary !== 'string') {
    return { ok: false, problems: ['结构化结果不完整'] }
  }
  const problems: string[] = []
  const l = sk.limits
  const opt = new Set(sk.optional_steps.map((s) => s.key))
  const seals = new Set(sk.seal_roadway_options.map((o) => o.roadway_id))
  const optional = r.optional_steps.map(String)
  const roads = (r.seal_roadways ?? []).map(String)
  for (const k of optional) if (!opt.has(k)) problems.push(`可选步骤不在清单里：${k}`)
  for (const k of roads) if (!seals.has(k)) problems.push(`密闭巷道不在清单里：${k}`)
  if (new Set(optional).size !== optional.length) problems.push('可选步骤有重复')
  if (new Set(roads).size !== roads.length) problems.push('密闭巷道有重复')
  if (hasSeals && !roads.length) problems.push('至少选一条密闭巷道')
  if (!hasSeals && roads.length) problems.push('本方案不封闭火区，不能选密闭巷道')
  const keys = new Set([...sk.required_steps.map((s) => s.key), ...optional])
  const seen = new Set<string>()
  const notes = r.step_notes.map((n: any) => ({ key: String(n?.key ?? ''), note: String(n?.note ?? '').trim() }))
  for (const n of notes) {
    if (!keys.has(n.key)) problems.push(`补充说明的步骤不在方案里：${n.key}`)
    if (seen.has(n.key)) problems.push(`步骤 ${n.key} 的补充说明重复`)
    seen.add(n.key)
    problems.push(...noteProblems(n.note, l.max_note_chars, `步骤 ${n.key} 的补充说明`, material))
  }
  const summary = r.summary.trim()
  problems.push(...noteProblems(summary, l.max_summary_chars, '方案概述', material))
  return problems.length ? { ok: false, problems } : { ok: true, selection: { optional_steps: optional, seal_roadways: roads, step_notes: notes.filter((n: any) => n.note), summary } }
}

export function reviewSchema(sc: Scope): Record<string, unknown> {
  return {
    type: 'object',
    additionalProperties: false,
    properties: {
      items: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          properties: {
            id: { type: 'string', enum: sc.items.map((i) => i.id) },
            conclusion: { type: 'string', enum: sc.conclusions.map((c) => c.id) },
            articles: { type: 'array', items: { type: 'string', enum: sc.articles.map((a) => a.article) } },
            note: { type: 'string' },
          },
          required: ['id', 'conclusion', 'articles', 'note'],
        },
      },
      summary: { type: 'string' },
    },
    required: ['items', 'summary'],
  }
}

export function reviewPrompt(opts: {
  skillName: string
  skillVersion: string
  skillBody: string
  scope: Scope
  planText: string
  redline: { ok: boolean; problems: string[] }
  articles: { document: string; article: string; content: string | null }[]
  flow?: string
}): string {
  const l = opts.scope.limits
  return [
    ROLE(REVIEW_STAGE, opts.flow ?? 'draft_sealing_plan'),
    '',
    '本阶段要求：',
    '- items：“审核项清单”的每一项都给一条：conclusion 从结论清单里选；articles 只能从“对照条文”的条号里选（可以为空）；' +
      `note 一句说明（不超过 ${l.max_note_chars} 字；判不通过时写清楚怎么改）；`,
    `- summary：一句审核意见（不超过 ${l.max_summary_chars} 字）。`,
    `- 只引用给定的条号，不凭记忆引用其他条文。${NUMBERS}`,
    '',
    `以下是技能 ${opts.skillName}（${opts.skillVersion}）的说明：`,
    '',
    opts.skillBody.trim(),
    '',
    '# 待审核的方案（代码生成）',
    '',
    opts.planText.trim(),
    '',
    `## 红线预检（actions，规则引擎）：${opts.redline.ok ? '通过' : '不通过'}`,
    '',
    ...(opts.redline.problems.length ? opts.redline.problems.map((p) => `- ${p}`) : ['- 无问题']),
    '',
    '## 审核项清单',
    '',
    ...opts.scope.items.map((i) => `- ${i.id}：${i.label}`),
    '',
    '## 结论清单',
    '',
    ...opts.scope.conclusions.map((c) => `- ${c.id}：${c.label}`),
    '',
    '## 对照条文（原文，条文索引）',
    '',
    ...opts.articles.map((a) => `- ${a.article}：${a.content ?? '（条文索引里没有这一条）'}`),
  ].join('\n')
}

/** 核对“合规审核”的结构化结果：逐项都有、编号合法、说明合规 */
export function checkReview(raw: unknown, sc: Scope, material: string): { ok: true; review: ReviewChoice } | { ok: false; problems: string[] } {
  const r = raw as any
  if (!r || typeof r !== 'object' || !Array.isArray(r.items) || typeof r.summary !== 'string') return { ok: false, problems: ['结构化结果不完整'] }
  const problems: string[] = []
  const ids = new Set(sc.items.map((i) => i.id))
  const concl = new Set(sc.conclusions.map((c) => c.id))
  const arts = new Set(sc.articles.map((a) => a.article))
  const seen = new Set<string>()
  const items = r.items.map((x: any) => ({
    id: String(x?.id ?? ''),
    conclusion: String(x?.conclusion ?? ''),
    articles: Array.isArray(x?.articles) ? x.articles.map(String) : [],
    note: String(x?.note ?? '').trim(),
  }))
  for (const x of items) {
    if (!ids.has(x.id)) problems.push(`审核项不在清单里：${x.id}`)
    if (seen.has(x.id)) problems.push(`审核项重复：${x.id}`)
    seen.add(x.id)
    if (!concl.has(x.conclusion)) problems.push(`审核项 ${x.id} 的结论不在清单里：${x.conclusion}`)
    for (const a of x.articles) if (!arts.has(a)) problems.push(`审核项 ${x.id} 引用了不在给定范围里的条文：${a}`)
    problems.push(...noteProblems(x.note, sc.limits.max_note_chars, `审核项 ${x.id} 的说明`, material))
  }
  const missing = sc.items.filter((i) => !seen.has(i.id)).map((i) => i.id)
  if (missing.length) problems.push(`审核项没有逐项给出结论：${missing.join('、')}`)
  const summary = r.summary.trim()
  problems.push(...noteProblems(summary, sc.limits.max_summary_chars, '审核意见', material))
  return problems.length ? { ok: false, problems } : { ok: true, review: { items, summary } }
}
