/**
 * 封闭方案的人工确认项：预设选项卡片（第五版）。契约 rescue_contract.field_conditions 导出的 data/field-conditions.json。
 * 5 项现场条件只能由人确认：卡片由代码给出（常用值做选项，也可以输入实际数值），操作员选的就是对现场的确认；
 * 智能体用 get_field_conditions 取卡片、用 ask_user 原样问，再把答案原样（标签或输入的文字）交给 draft_sealing_plan 的
 * field_choices，由这里换成数值或 true/false——换不了就报错让智能体重问，不让它猜。
 */

import { readFileSync } from 'node:fs'

import type { HumanInput } from './sealing.ts'

export interface FieldChoice {
  label: string
  value: number | boolean
}
export interface FieldDef {
  field: string
  value_type: 'number' | 'bool'
  question: string
  choices: FieldChoice[]
}
export interface FieldConditions {
  note: string
  confirmers: string[]
  fields: FieldDef[]
}

export function loadFieldConditions(file = new URL('../../data/field-conditions.json', import.meta.url)): FieldConditions {
  const data = JSON.parse(readFileSync(file, 'utf8')) as FieldConditions
  if (!Array.isArray(data.fields) || data.fields.length === 0) throw new Error('field-conditions.json 里没有 fields')
  return data
}

/** get_field_conditions 的返回：给 ask_user 的问题（原样用）和怎么把答案交回流程 */
export function describeFieldCard(fc: FieldConditions) {
  return {
    questions: [
      ...fc.fields.map((f) => ({ id: f.field, header: '现场条件', question: f.question, multiSelect: false, options: f.choices.map((c) => c.label) })),
      { id: 'confirmed_by', header: '现场条件', question: '以上现场条件由谁确认？', multiSelect: false, options: fc.confirmers },
    ],
    note:
      `${fc.note} 用 ask_user 把 questions 原样问给操作员（一次问完）；然后调 draft_sealing_plan，field_choices 里每项给 {field: 问题 id, choice: 答案原文（选的标签或输入的文字，不要改写）}，` +
      'confirmed_by 填“确认人”那题的答案。某题被跳过就不要传那一项，不要替人填；全部跳过就不传 field_choices，照常编制（弹窗里会列出缺少的现场条件，由确认人把关）。',
  }
}

/** 答案原文 → 方案的人工输入；不认识的字段、换不成数值或 true/false 的、缺确认人的都报错（让智能体重问，不让它猜） */
export function resolveFieldChoices(fc: FieldConditions, choices: { field: string; choice: string }[], confirmedBy: string | undefined): HumanInput[] {
  if (!choices.length) return []
  const who = (confirmedBy ?? '').trim()
  if (!who) throw new Error('field_choices 需要 confirmed_by（确认人）：用卡片里“确认人”那题的答案，没有就重新问')
  const byField = new Map(fc.fields.map((f) => [f.field, f]))
  const seen = new Set<string>()
  return choices.map(({ field, choice }) => {
    const def = byField.get(field)
    if (!def) throw new Error(`field_choices 里的 ${field} 不是现场条件：只能是 ${[...byField.keys()].join('、')}`)
    if (seen.has(field)) throw new Error(`field_choices 里 ${field} 重复`)
    seen.add(field)
    const text = String(choice ?? '').trim()
    const hit = def.choices.find((c) => c.label === text)
    if (hit) return { field, value: hit.value, confirmed_by: who }
    if (def.value_type === 'number') {
      const n = Number(text.replace(/\s*(℃|度|米|m)\s*$/i, ''))
      if (text !== '' && Number.isFinite(n)) return { field, value: n, confirmed_by: who }
      throw new Error(`${field} 的答案“${text}”不是数值，也不是卡片上的选项：请操作员重新选择或输入数值`)
    }
    throw new Error(`${field} 的答案“${text}”不是“是”或“否”：请操作员重新选择`)
  })
}
