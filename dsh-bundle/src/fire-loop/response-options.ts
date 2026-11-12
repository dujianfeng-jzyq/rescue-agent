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
  /** 卡片上标“（建议）”，仍由操作员选 */
  suggested?: boolean
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

/**
 * 2026-11-11 改（用户决定）：注氮不再必做，卡片列出全部可选处置（注氮标“建议”）。
 * 都不采取是危险的决定，要操作员在卡片的输入框里亲手输入 NONE_PHRASE 再提交（代码逐字比对，不由智能体解释文字）；
 * 点“跳过”只算暂不决定（容易误点）；勾了处置又输入 NONE_PHRASE 算冲突，重问。
 */
export const NONE_PHRASE = '不采取处置'
export const SUGGESTED_SUFFIX = '（建议）'

/** 输入框的文字是不是 NONE_PHRASE：去掉首尾空白、两边引号和句末标点后逐字相等 */
export const isNonePhrase = (text: string): boolean =>
  text
    .trim()
    .replace(/^[“”"'‘’「」]+|[“”"'‘’「」]+$/g, '')
    .replace(/[。．.！!；;，,]+$/, '')
    .trim() === NONE_PHRASE

const cardLabel = (o: ResponseOption): string => o.label + (o.suggested ? SUGGESTED_SUFFIX : '')

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
            `${LEVEL_NAME[level] ?? level}预警：请选择要采取的处置（可多选；标“建议”的是建议采取的）。都不采取请在输入框输入“${NONE_PHRASE}”后提交（不要同时勾选处置）；点“跳过”表示暂不决定，不下发任何处置，之后再问` +
            (always.length ? `。必做项已包含，不用选：${always.map((o) => o.label).join('、')}` : ''),
          multi_select: true,
          options: choosable.map((o) => ({ label: cardLabel(o) })),
        }
  const labelToId = card ? Object.fromEntries(choosable.map((o) => [cardLabel(o), o.id])) : null
  return {
    level,
    options: all.map((o) => ({ id: o.id, label: o.label, always: o.always, choosable: o.choosable, suggested: Boolean(o.suggested), how: o.how })),
    card,
    label_to_id: labelToId,
    note: card
      ? `用 ask_user 提问：questions 里只放 card 这一道题，原样照抄（id、选项和标签不增不减、不改、不加说明）。拿到答案后调 record_response_choice（panel、level；答案里的 selected 原样放进 selected，答案里有 custom 就原样放进 custom，一个字不改；答案是跳过（selected 为空且没有 custom）就传 skipped: true），由它判断操作员选了什么、记进事件时间线，再按它返回的 next 做；不要自己解释答案，也不要追问。`
      : '这一级的各项处置都要落实，由你按各选项的 how 在同一轮里提交（不问“要不要”，确认弹窗就是请示），不用向操作员列选项。',
  }
}

export type ChoiceKind = 'measures' | 'none' | 'undecided' | 'conflict'

/**
 * 操作员在卡片上的答案 → 选择（代码判断，智能体照办）：
 * - 勾了处置 → measures（输入框另有别的文字只记下，不影响）；
 * - 没勾处置、输入框是“不采取处置” → none；
 * - 勾了处置又输入“不采取处置” → conflict（不下发，立即重问）；
 * - 点了跳过、没有答案、或只有认不出的文字 → undecided（不下发，下次唤醒再问）。
 */
export function interpretChoice(levels: Levels, levelRaw: string, selected: string[], custom: string, skipped: boolean) {
  const level = normalizeLevel(levelRaw)
  const choosable = (levels[level] ?? []).filter((o) => o.choosable && !o.always)
  const byLabel = new Map<string, ResponseOption>()
  for (const o of choosable) {
    byLabel.set(cardLabel(o), o)
    byLabel.set(o.label, o)
  }
  const picked: ResponseOption[] = []
  const unknown: string[] = []
  let none = false
  const typed = skipped ? '' : custom.trim()
  if (typed) {
    if (isNonePhrase(typed)) none = true
    else unknown.push(typed)
  }
  for (const raw of skipped ? [] : selected) {
    const label = String(raw).trim()
    if (byLabel.has(label)) picked.push(byLabel.get(label)!)
    else if (isNonePhrase(label)) none = true // 智能体把输入的文字放进了 selected：照样认
    else if (label) unknown.push(label)
  }
  const ids = choosable.filter((o) => picked.includes(o)).map((o) => o.id)
  const choice: ChoiceKind = ids.length ? (none ? 'conflict' : 'measures') : none ? 'none' : 'undecided'
  const labels = choosable.filter((o) => ids.includes(o.id)).map((o) => o.label)
  const text =
    choice === 'measures'
      ? `操作员选择：${labels.join('、')}` + (unknown.length ? `（另附文字：${unknown.join('；')}）` : '')
      : choice === 'none'
        ? `操作员输入“${NONE_PHRASE}”：不采取处置，只继续监测`
        : choice === 'conflict'
          ? `操作员勾选了 ${labels.join('、')}，又输入了“${NONE_PHRASE}”，互相冲突，没有采纳`
          : `操作员暂未选择处置（${skipped ? '点了跳过' : unknown.length ? `输入的文字不是“${NONE_PHRASE}”，也没有勾选处置：${unknown.join('；')}` : '没有答案'}）`
  const next =
    choice === 'measures'
      ? `调 draft_firefighting_plan，selected_options 传 ${JSON.stringify(ids)}（原样传，不增不减），审核通过后经 issue_firefighting_plan 提交人工确认下发。`
      : choice === 'none'
        ? '操作员明确不采取处置：不编方案、不下发任何处置；在回答里如实说明，登记跟踪（add_followup）继续观察。之后复评恶化时 get_response_options 会再给卡片。'
        : choice === 'conflict'
          ? `选择冲突：不编方案、不下发。立即再调 get_response_options（带 panel），用 ask_user 把返回的 card 原样再问一次（卡片上会说明不能同时选）；不要自己替操作员选。`
          : '操作员还没有决定：不编方案、不下发任何处置；在回答里说明可以随时要求重新给出处置选项，登记跟踪（add_followup）。下次被唤醒时 get_response_options 会再给卡片。' +
            (unknown.length ? `输入的文字不当作处置选择，不要按它自己定处置；要都不采取须输入“${NONE_PHRASE}”。` : '')
  return { level, choice, option_ids: ids, labels, unknown, text, next }
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

/** 每一级“选过处置”的凭据：该级的方案经 guard 下发成功（事件时间线里 guard 写的 action note），或操作员输入“不采取处置”（record_response_choice 写的 note） */
const PLAN_TOOL: Record<string, string> = { orange: 'issue_firefighting_plan' }
const ISSUED = new Set(['issued', 'completed'])

interface TimelineEntry {
  at: string
  kind: string
  by: string
  data?: Record<string, any>
}

const isIssued = (e: TimelineEntry, tool: string) => e.kind === 'note' && e.by === 'guard' && e.data?.type === 'action' && e.data?.tool === tool && ISSUED.has(String(e.data?.status))
const choiceOf = (e: TimelineEntry, level: string): ChoiceKind | undefined =>
  e.kind === 'note' && e.data?.type === 'response_choice' && normalizeLevel(String(e.data?.level ?? '')) === level ? (e.data?.choice as ChoiceKind) : undefined

/**
 * 这一级是否已经选过处置、不用再问（代码判断，智能体照办）。看最近一次“决定”：方案下发、操作员输入“不采取处置”、操作员暂未决定或选择冲突。
 * - 暂未决定或冲突 → 要问（冲突时 conflict 为 true，卡片上提醒）；
 * - 下发了方案或选了不处置 → 之后只要复评没有给出“恶化”就不再问；最近一次复评是恶化 → 要问（追加处置）；
 * - 都没有 → 照常问。
 * 火区封闭方案已下发（之后没有启封）→ 不问：封闭后不再选注氮、注浆、堵漏这类橙色处置，按封闭后的流程跟踪
 * （2026-11-12 演示：封闭注惰后乙烯被稀释，研判一度为橙色；那时复评若是恶化，原来会弹出橙色处置卡片）。
 */
export function priorChoice(entries: TimelineEntry[], levelRaw: string): { ask: boolean; issued_at?: string; declined_at?: string; sealed_at?: string; conflict?: boolean; review?: string; reason: string } {
  const level = normalizeLevel(levelRaw)
  const tool = PLAN_TOOL[level]
  if (!tool) return { ask: true, reason: '这一级不按卡片选处置' }
  const sealed = entries.findLastIndex((e) => isIssued(e, 'issue_sealing_plan'))
  if (sealed >= 0 && !entries.slice(sealed + 1).some((e) => isIssued(e, 'approve_unsealing'))) {
    const at = entries[sealed]!.at
    return { ask: false, sealed_at: at, reason: `${at} 已下发火区封闭方案，封闭后不再选橙色处置` }
  }
  const i = entries.findLastIndex((e) => isIssued(e, tool) || ['none', 'undecided', 'conflict'].includes(choiceOf(e, level) ?? ''))
  if (i < 0) return { ask: true, reason: '这一级还没有下发过方案，操作员也没有选过' }
  const last = entries[i]!
  if (choiceOf(last, level) === 'undecided') return { ask: true, reason: `${last.at} 操作员暂未选择处置，再问一次` }
  if (choiceOf(last, level) === 'conflict') return { ask: true, conflict: true, reason: `${last.at} 操作员的选择互相冲突（勾了处置又输入“${NONE_PHRASE}”），再问一次` }
  const declined = choiceOf(last, level) === 'none'
  const what = declined ? `${last.at} 操作员选择不采取处置` : `${last.at} 已下发本级方案`
  const mark = declined ? { declined_at: last.at } : { issued_at: last.at }
  const review = entries.slice(i + 1).findLast((e) => e.kind === 'note' && e.data?.type === 'effect_review')
  if (review?.data?.overall === 'worsening') return { ask: true, ...mark, review: review.at, reason: `${what}，${review.at} 的复评结论为恶化，问操作员要采取哪些处置` }
  return { ask: false, ...mark, ...(review ? { review: review.at } : {}), reason: review ? `${what}，之后的复评（${review.at}）没有恶化` : `${what}，之后还没有复评` }
}

export type PriorChoice = ReturnType<typeof priorChoice>

/**
 * 按“这一级之前的决定”调整 get_response_options 的结果（代码定，智能体原样问）：
 * - 不用再问 → 去掉卡片，note 说明为什么；
 * - 上次选择冲突 → 卡片上提醒只选一种；
 * - 已下发过方案、复评恶化 → 提醒已下发的不用再选（代码不过滤：再选会随新方案再下发一次）；
 * - 上次选了不处置、复评恶化 → 卡片上说明是因为情况变了才再问（2026-11-11：不是重复打扰）。
 */
export function applyPriorChoice<T extends { card: any; note?: string; prior?: PriorChoice }>(out: T, prior: PriorChoice): T {
  out.prior = prior
  if (!prior.ask) {
    out.card = null
    out.note = prior.sealed_at
      ? `${prior.reason}：不问操作员，也不下发防灭火方案；按封闭后的流程跟踪（登记复评）。`
      : prior.declined_at
      ? `${prior.reason}：不再问操作员，也不下发处置；继续跟踪（登记复评）。复评结论为恶化时再调本工具，它会给出卡片。`
      : `${prior.reason}：不再问操作员，也不重新下发同样的方案；继续按已下发的方案跟踪（登记复评）。复评结论为恶化时再调本工具，它会给出追加处置的卡片。`
    return out
  }
  if (!out.card) return out
  const add = (text: string) => {
    out.card = { ...out.card, question: `${out.card.question}。${text}` }
    out.note = `${prior.reason}。${out.note ?? ''}`
  }
  if (prior.conflict) add(`上次既勾选了处置又输入了“${NONE_PHRASE}”，请只选一种`)
  else if (prior.issued_at) add(`本级已下发过方案（${prior.issued_at}），已下发的处置不用再选`)
  else if (prior.declined_at && prior.review) add(`上次（${prior.declined_at}）选择不采取处置；此后 ${prior.review} 的复评结论为恶化，所以再问一次`)
  return out
}
