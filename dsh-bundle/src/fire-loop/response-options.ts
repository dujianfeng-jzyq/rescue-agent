/**
 * 处置选项表（第五版）：契约 rescue_contract.response_options 导出的 data/response-options.json。
 * 每一级预警操作员可以选哪些处置；智能体用 get_response_options 取，不自己编选项；
 * 防灭火方案流程用它把操作员选的选项编号换成方案步骤 key（代码校验，选项不在表里或不能选就报错）。
 */

import { readFileSync } from 'node:fs'

export interface ResponseOption {
  id: string
  label: string
  step: string
  how: string
  plan_key: string | null
  always: boolean
  choosable: boolean
}

export type Levels = Record<string, ResponseOption[]>

export function loadResponseOptions(file = new URL('../../data/response-options.json', import.meta.url)): Levels {
  const data = JSON.parse(readFileSync(file, 'utf8')) as { levels?: Levels }
  if (!data.levels || Object.keys(data.levels).length === 0) throw new Error('response-options.json 里没有 levels')
  return data.levels
}

const NAME_TO_LEVEL: Record<string, string> = { 黄色: 'yellow', 橙色: 'orange', 红色: 'red' }
const LEVEL_NAME: Record<string, string> = { yellow: '黄色', orange: '橙色', red: '红色' }

export const normalizeLevel = (level: string): string => NAME_TO_LEVEL[level] ?? level

/** get_response_options 的返回：选项（代码给出）、给智能体的提问卡片内容（标签原样用）、选了之后怎么落实 */
export function describeOptions(levels: Levels, levelRaw: string) {
  const level = normalizeLevel(levelRaw)
  const all = levels[level] ?? []
  if (all.length === 0) {
    return { level, options: [], card: null, note: `${levelRaw} 没有处置选项：只有黄色、橙色、红色预警才有。` }
  }
  const choosable = all.filter((o) => o.choosable && !o.always)
  const always = all.filter((o) => o.always)
  const card =
    choosable.length === 0
      ? null
      : {
          header: '处置选项',
          question: `${LEVEL_NAME[level] ?? level}预警：请选择要追加的处置（可多选；不追加、只做必做项就点“跳过”）`,
          detail: always.length ? `必做项已包含，不用选：${always.map((o) => o.label).join('、')}` : null,
          multiSelect: true,
          options: choosable.map((o) => o.label),
          label_to_id: Object.fromEntries(choosable.map((o) => [o.label, o.id])),
        }
  return {
    level,
    options: all.map((o) => ({ id: o.id, label: o.label, always: o.always, choosable: o.choosable, how: o.how })),
    card,
    note: card
      ? '用 ask_user 把 card 原样问给操作员（标签照抄，multiSelect 为 true）；把答案里选中的标签按 label_to_id 换成 id，调 draft_firefighting_plan 的 selected_options 传入（必做项不用传）；操作员点了“跳过”（没有选任何项）就是只做必做项，selected_options 传空列表 []，不要再追问。没有 card（选项由流程自己落实）时不用问。'
      : '这一级的处置由对应流程自己落实（见各选项的 how），不用向操作员列选项。',
  }
}

/** 选项编号 → 防灭火方案的可选步骤 key（按表里的顺序）；不认识或不能选的报错 */
export function planKeysFor(levels: Levels, level: string, ids: string[]): string[] {
  const known = new Map((levels[normalizeLevel(level)] ?? []).map((o) => [o.id, o]))
  const keys: string[] = []
  for (const id of ids) {
    const o = known.get(id)
    if (!o) throw new Error(`选项 ${id} 不在 ${level} 的处置选项表里：用 get_response_options 取选项，不要自己编`)
    if (o.always) continue // 必做项本来就在方案里
    if (!o.choosable || !o.plan_key) throw new Error(`选项 ${id}（${o.label}）不由卡片选择，按 ${o.how} 落实`)
    keys.push(o.plan_key)
  }
  return [...new Set(keys)]
}
