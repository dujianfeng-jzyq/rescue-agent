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

/** 卡片里“不追加”的选项（代码给出，2026-11-10 演示复看：智能体自己加“跳过”选项，不如由代码给）；label_to_id 里对应 null */
export const NONE_LABEL = '不追加（只做必做项）'

/** get_response_options 的返回：选项（代码给出）、给智能体的提问卡片内容（标签原样用）、选了之后怎么落实 */
export function describeOptions(levels: Levels, levelRaw: string) {
  const level = normalizeLevel(levelRaw)
  const all = levels[level] ?? []
  if (all.length === 0) {
    return { level, options: [], card: null, note: `${levelRaw} 没有处置选项：只有黄色、橙色、红色预警才有。` }
  }
  const choosable = all.filter((o) => o.choosable && !o.always)
  const always = all.filter((o) => o.always)
  // card 就是 ask_user 的一道题，字段名和结构与 ask_user 的参数一致（选项是 {label} 对象、multi_select），智能体放进 questions 原样传
  const card =
    choosable.length === 0
      ? null
      : {
          id: 'orange_response_options',
          header: '处置选项',
          question:
            `${LEVEL_NAME[level] ?? level}预警：请选择要追加的处置（可多选；不追加就选“${NONE_LABEL}”）` +
            (always.length ? `。必做项已包含，不用选：${always.map((o) => o.label).join('、')}` : ''),
          multi_select: true,
          options: [...choosable.map((o) => ({ label: o.label })), { label: NONE_LABEL }],
        }
  const labelToId = card ? { ...Object.fromEntries(choosable.map((o) => [o.label, o.id])), [NONE_LABEL]: null } : null
  return {
    level,
    options: all.map((o) => ({ id: o.id, label: o.label, always: o.always, choosable: o.choosable, how: o.how })),
    card,
    label_to_id: labelToId,
    note: card
      ? `用 ask_user 提问：questions 里只放 card 这一道题，原样照抄（id、选项和标签不增不减、不改、不加说明）；把答案里选中的标签按 label_to_id 换成 id（“${NONE_LABEL}”对应 null，去掉），调 draft_firefighting_plan 的 selected_options 传入（必做项不用传）。操作员选了“${NONE_LABEL}”或点了“跳过”就是只做必做项，selected_options 传空列表 []，不要再追问；“${NONE_LABEL}”和别的选项一起选时以别的选项为准。没有 card（选项由流程自己落实）时不用问。`
      : '这一级的各项处置都要落实，由你按各选项的 how 在同一轮里提交（不问“要不要”，确认弹窗就是请示），不用向操作员列选项。',
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

/** 每一级“选过处置”的凭据：该级的方案经 guard 下发成功（事件时间线里 guard 写的 action note） */
const PLAN_TOOL: Record<string, string> = { orange: 'issue_firefighting_plan' }
const ISSUED = new Set(['issued', 'completed'])

interface TimelineEntry {
  at: string
  kind: string
  by: string
  data?: Record<string, any>
}

/**
 * 这一级是否已经选过处置、不用再问（代码判断，智能体照办）：该级方案下发之后，只要复评没有给出“恶化”就不再问；
 * 下发之后最近一次复评是恶化 → 要问（追加处置）。没有下发过 → 照常问。
 */
export function priorChoice(entries: TimelineEntry[], levelRaw: string): { ask: boolean; issued_at?: string; review?: string; reason: string } {
  const tool = PLAN_TOOL[normalizeLevel(levelRaw)]
  if (!tool) return { ask: true, reason: '这一级不按卡片选处置' }
  const i = entries.findLastIndex((e) => e.kind === 'note' && e.by === 'guard' && e.data?.type === 'action' && e.data?.tool === tool && ISSUED.has(String(e.data?.status)))
  if (i < 0) return { ask: true, reason: '这一级还没有下发过方案' }
  const issuedAt = entries[i]!.at
  const review = entries.slice(i + 1).findLast((e) => e.kind === 'note' && e.data?.type === 'effect_review')
  if (review?.data?.overall === 'worsening') return { ask: true, issued_at: issuedAt, review: review.at, reason: `${issuedAt} 下发方案后，${review.at} 的复评结论为恶化，问操作员追加哪些处置` }
  return {
    ask: false,
    issued_at: issuedAt,
    ...(review ? { review: review.at } : {}),
    reason: review ? `${issuedAt} 已下发本级方案，之后的复评（${review.at}）没有恶化` : `${issuedAt} 已下发本级方案，之后还没有复评`,
  }
}
