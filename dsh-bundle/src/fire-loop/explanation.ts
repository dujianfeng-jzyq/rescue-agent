/**
 * 阶段“研判说明”（design-v2 4.4 第 3 点，第五稿审定第 2 条）。
 *
 * 模型只做选择：从 explanation_options.py 给出的关注点、下一步清单里选编号，每项写一句说明，再写一句总述。
 * 阶段指令 = fire-assessment 的 SKILL.md 正文（每次运行读当前版本）+ 本阶段说明；输入是报告事实部分和清单。
 * 代码检查：编号来自清单、不重复、条数和字数不超限、说明文字里的数值都能在本次材料里找到。不合格就判失败，
 * 报告只含事实部分（流程照常完成）。这里只放纯逻辑；起子 Agent 的部分由 index.ts 注入（StageRunner）。
 */

export interface Option {
  id: string
  label: string
}

export interface Limits {
  max_focus: number
  max_next_steps: number
  max_note_chars: number
  max_summary_chars: number
}

export interface Options {
  focus_options: Option[]
  next_step_options: Option[]
  limits: Limits
}

export interface Explanation {
  summary: string
  focus: { id: string; label: string; note: string }[]
  next_steps: { id: string; label: string; reason: string }[]
}

/** 起子 Agent 跑一次阶段：返回结构化结果，或失败原因 */
export type StageRunner = (req: { label: string; prompt: string; schema: Record<string, unknown>; signal?: AbortSignal }) => Promise<
  { ok: true; structured: unknown; childId: string } | { ok: false; error: string; childId?: string }
>

export const STAGE = '研判说明'

export function stageSchema(o: Options): Record<string, unknown> {
  const item = (ids: string[], text: string) => ({
    type: 'object',
    additionalProperties: false,
    properties: { id: { type: 'string', enum: ids }, [text]: { type: 'string' } },
    required: ['id', text],
  })
  return {
    type: 'object',
    additionalProperties: false,
    properties: {
      summary: { type: 'string' },
      focus: { type: 'array', items: item(o.focus_options.map((x) => x.id), 'note') },
      next_steps: { type: 'array', items: item(o.next_step_options.map((x) => x.id), 'reason') },
    },
    required: ['summary', 'focus', 'next_steps'],
  }
}

export function stagePrompt(opts: { skillName: string; skillVersion: string; skillBody: string; facts: string; options: Options }): string {
  const { limits: l } = opts.options
  return [
    `你是固定流程 run_fire_assessment 的“${STAGE}”阶段。只用 structured_output 提交结果，不要调用任何其他工具，不要向人提问。`,
    '',
    '本阶段要求：',
    `- 从“关注点清单”里选 1–${l.max_focus} 项最需要指挥人员注意的（按重要性排序），每项 note 写一句说明（不超过 ${l.max_note_chars} 字，可以为空）；`,
    `- 从“下一步清单”里选 1–${l.max_next_steps} 项，每项 reason 写一句理由（不超过 ${l.max_note_chars} 字，可以为空）；`,
    `- summary 写一句总述（不超过 ${l.max_summary_chars} 字）。`,
    '- 只能用清单里的 id。说明、理由、总述里不写清单和报告里没有的数值：不换算、不估算、不相加、不计数、不归纳界限；不写序号；不引用条文；不声称已执行任何处置。',
    '- 写不出合规的说明时，note、reason 留空字符串。',
    '',
    `以下是技能 ${opts.skillName}（${opts.skillVersion}）的说明，供判断时参考：`,
    '',
    opts.skillBody.trim(),
    '',
    '## 本次研判报告（事实部分，代码生成）',
    '',
    opts.facts.trim(),
    '',
    '## 关注点清单',
    '',
    ...opts.options.focus_options.map((x) => `- ${x.id}：${x.label}`),
    '',
    '## 下一步清单',
    '',
    ...opts.options.next_step_options.map((x) => `- ${x.id}：${x.label}`),
  ].join('\n')
}

// 数值核对：编号（BT-1201-G1、RS-…）、化学式和测点名（C2H4、G1）、条号、日期时刻里的数字不算数值
const STRIP = [
  /\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?/g,
  /(?<![\d.])\d{1,2}:\d{2}(?::\d{2})?(?![\d.])/g,
  /[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+/g,
  /(?<![A-Za-z0-9])[A-Za-z]+\d+[A-Za-z0-9]*/g,
  /第\s*\d+(?:\s*[、，,和及与至—-]\s*\d+)*\s*条/g, // 条号，含“第84、85、86条”这样的并列写法
]
const NUM = /\d+(?:\.\d+)?/g

export function numbersIn(text: string): string[] {
  let t = text
  for (const rx of STRIP) t = t.replace(rx, (m) => ' '.repeat(m.length))
  return t.match(NUM) ?? []
}

/** 文字里出现、材料里找不到的数值（按数值比较：1.50 与 1.5 相同） */
export function ungrounded(text: string, material: string): string[] {
  const pool = new Set(numbersIn(material).map((x) => Number(x)))
  return numbersIn(text).filter((x) => !pool.has(Number(x)))
}

/** 核对模型的结构化结果，返回解析后的说明或问题清单 */
export function checkExplanation(raw: unknown, o: Options, material: string): { ok: true; explanation: Explanation } | { ok: false; problems: string[] } {
  const problems: string[] = []
  const r = raw as any
  if (!r || typeof r !== 'object' || typeof r.summary !== 'string' || !Array.isArray(r.focus) || !Array.isArray(r.next_steps)) {
    return { ok: false, problems: ['结构化结果不完整'] }
  }
  const l = o.limits
  const resolve = (list: any[], options: Option[], text: 'note' | 'reason', max: number, what: string) => {
    const byId = new Map(options.map((x) => [x.id, x.label]))
    const seen = new Set<string>()
    if (list.length < 1 || list.length > max) problems.push(`${what}应选 1–${max} 项，实际 ${list.length} 项`)
    return list.map((x) => {
      const id = String(x?.id ?? '')
      const words = String(x?.[text] ?? '').trim()
      if (!byId.has(id)) problems.push(`${what}的编号不在清单里：${id}`)
      if (seen.has(id)) problems.push(`${what}重复：${id}`)
      seen.add(id)
      if (words.length > l.max_note_chars) problems.push(`${what} ${id} 的说明超过 ${l.max_note_chars} 字`)
      const bad = ungrounded(words, material)
      if (bad.length) problems.push(`${what} ${id} 的说明里有记录中找不到的数值：${bad.join('、')}`)
      return { id, label: byId.get(id) ?? id, [text]: words }
    })
  }
  const focus = resolve(r.focus, o.focus_options, 'note', l.max_focus, '关注点') as Explanation['focus']
  const next = resolve(r.next_steps, o.next_step_options, 'reason', l.max_next_steps, '下一步') as Explanation['next_steps']
  const summary = r.summary.trim()
  if (!summary) problems.push('总述为空')
  if (summary.length > l.max_summary_chars) problems.push(`总述超过 ${l.max_summary_chars} 字`)
  const bad = ungrounded(summary, material)
  if (bad.length) problems.push(`总述里有记录中找不到的数值：${bad.join('、')}`)
  return problems.length ? { ok: false, problems } : { ok: true, explanation: { summary, focus, next_steps: next } }
}
