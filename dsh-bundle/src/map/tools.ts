/**
 * map_view：智能体控制地图（docs/design-v4-map.md 第 4 节）。只做展示，不写矿井、不弹人工确认。
 *
 * 守住“数值只来自代码”：
 *   - 名称只能是地图上有的（先用 list 查）；找不到时返回相近的名称，不猜；
 *   - 带 record_id 的标记，标签文字和颜色由代码从记录里取（等级、阶段、报警项数），模型不写数字；
 *   - 没有记录依据的标记只能是提示色，备注 note 只能从封闭词表里选（不是自由文字：AI 在图上写“这是撤人通道”之类的断言，操作员可能当真）；
 *   - 智能体的标记编号都是 agent:，最多 12 个；系统自动标的（auto:）智能体动不了，也不能在同一位置再标（等级会过时、两个圈叠在一起）。
 * 预警等级、矿井报警、火区由系统自动标在图上（autopilot），不用智能体重复标。
 */

import type { MapCommand, MapHub, MapMark } from './hub.ts'
import { AGENT_PREFIX, AUTO_PREFIX, levelOf } from './autopilot.ts'
import { labelFromRecord } from './record-label.ts'
import type { RecordLike } from './record-label.ts'

export { AGENT_PREFIX }
export const MAX_AGENT_MARKS = 12
/**
 * 备注的封闭词表：只说“看这里”，不对位置作任何断言（不说是撤人通道、不说该怎么处置、不带数值）。
 * 具体的数值、等级、阶段、报警项数只能经 record_id 由代码从记录里取。
 */
export const NOTES = ['问题所在', '重点关注', '需要核查', '受影响区域', '正在查看'] as const
const RECORD_ID_RE = /^(EV|RS|RP)-\d{8}T\d{6}-[a-z0-9]{6}$/
const LIST_LIMIT = 30

export const ACTIONS = ['list', 'focus', 'mark', 'unmark', 'clear', 'view', 'reset'] as const
export type Action = (typeof ACTIONS)[number]

export interface MapViewArgs {
  action: Action
  name?: string
  record_id?: string
  /** 从 NOTES 里选；不是自由文字 */
  note?: string
  zoom?: number
  tilt?: number
  query?: string
}

export interface MapViewDeps {
  hub: MapHub
  /** 地图上的名称；没有几何数据时为空集（所有按名称的动作都会被拒绝） */
  names: ReadonlySet<string>
  readRecord: (id: string) => Promise<RecordLike | null>
}

/** 相近的名称：互相包含的排前面，再按前缀相同的字数排。 */
export function nearest(names: Iterable<string>, query: string, limit = 5): string[] {
  const q = query.trim()
  if (!q) return []
  const score = (n: string): number => {
    if (n === q) return 1000
    if (n.includes(q) || q.includes(n)) return 500 + Math.min(n.length, q.length)
    let k = 0
    while (k < n.length && k < q.length && n[k] === q[k]) k++
    // 共同的字符数（不计顺序）作为次要依据
    const common = [...new Set(q)].filter((ch) => n.includes(ch)).length
    return k * 10 + common
  }
  return [...names]
    .map((n) => ({ n, s: score(n) }))
    .filter((x) => x.s >= 12)
    .sort((a, b) => b.s - a.s || a.n.length - b.n.length)
    .slice(0, limit)
    .map((x) => x.n)
}

function requireName(args: MapViewArgs, names: ReadonlySet<string>): string {
  const name = args.name?.trim()
  if (!name) throw new Error('需要 name：地图上的名称（先用 action=list 查）')
  if (!names.has(name)) {
    const close = nearest(names, name)
    throw new Error(`地图上没有叫“${name}”的位置。${close.length ? `相近的有：${close.join('、')}。` : ''}用 action=list 加 query 查。`)
  }
  return name
}

export async function runMapView(args: MapViewArgs, deps: MapViewDeps): Promise<Record<string, unknown>> {
  const { hub, names } = deps
  const mine = (): MapMark[] => hub.snapshot().marks.filter((m) => m.id.startsWith(AGENT_PREFIX))
  const view = (): Record<string, unknown> => ({ agent_marks: mine().map((m) => ({ id: m.id, name: m.name, text: m.text })) })

  switch (args.action) {
    case 'list': {
      const q = args.query?.trim() ?? ''
      const pool = q ? [...names].filter((n) => n.includes(q) || nearest([n], q).length > 0) : [...names]
      const sorted = q ? nearest(pool, q, LIST_LIMIT) : pool.sort().slice(0, LIST_LIMIT)
      let note: string | undefined
      if (q && sorted.length === 0) note = '地图上没有名称包含或接近这个关键词。如果操作员要的位置不在地图名称里，如实告诉他地图上没有，不要换说法反复查，也不要用别的名称代替。'
      else if (!q && sorted.length < names.size) note = `只列了前 ${sorted.length} 个，加 query 缩小范围`
      return { count: names.size, shown: sorted.length, names: sorted, ...(note ? { note } : {}) }
    }
    case 'focus': {
      const name = requireName(args, names)
      const cmd: MapCommand = { op: 'focus', name, zoom: args.zoom ?? 4 }
      hub.emit(cmd)
      return { ok: true, focused: name }
    }
    case 'mark': {
      const name = requireName(args, names)
      const note = args.note?.trim() ?? ''
      if (note && !(NOTES as readonly string[]).includes(note)) {
        throw new Error(`note 只能从这几个里选：${NOTES.join('、')}。不能自由写：对位置的判断（撤人路线、处置建议）和数值只来自记录，请给 record_id，标签由系统从记录里取，其余在回答里说明`)
      }
      const auto = hub.snapshot().marks.find((m) => m.id === `${AUTO_PREFIX}${name}`)
      if (auto) {
        throw new Error(`“${name}”已由系统自动标出（${auto.text}），随事件状态更新，不要再标：你按记录标的等级不会更新，会和系统的标记叠在一起。要让操作员看这里，用 action=focus`)
      }
      const id = `${AGENT_PREFIX}${name}`
      if (!mine().some((m) => m.id === id) && mine().length >= MAX_AGENT_MARKS) throw new Error(`你的标记已有 ${MAX_AGENT_MARKS} 个，先用 unmark 或 clear 清掉一些`)
      let level = 'info'
      let from = ''
      if (args.record_id) {
        if (!RECORD_ID_RE.test(args.record_id)) throw new Error(`record_id 格式不对：${args.record_id}`)
        const rec = await deps.readRecord(args.record_id)
        if (!rec) throw new Error(`找不到记录 ${args.record_id}：只能引用已经生成的记录`)
        const lab = labelFromRecord(rec)
        from = lab.text
        if (lab.warningLevel) level = levelOf(lab.warningLevel)
      }
      const text = [note, from].filter(Boolean).join(' · ')
      if (!text) throw new Error(`需要 note（从 ${NOTES.join('、')} 里选）或 record_id（标签由系统从记录里取）`)
      hub.emit({ op: 'mark', id, name, level, text })
      if (args.zoom !== undefined) hub.emit({ op: 'focus', name, zoom: args.zoom })
      return { ok: true, marked: name, text, level, ...view() }
    }
    case 'unmark': {
      const name = requireName(args, names)
      const id = `${AGENT_PREFIX}${name}`
      if (!mine().some((m) => m.id === id)) throw new Error(`你没有在“${name}”上标记`)
      hub.emit({ op: 'unmark', id })
      return { ok: true, unmarked: name, ...view() }
    }
    case 'clear': {
      for (const m of mine()) hub.emit({ op: 'unmark', id: m.id })
      return { ok: true, ...view() }
    }
    case 'view': {
      if (args.tilt === undefined) throw new Error('需要 tilt（0 到 60 度）')
      hub.emit({ op: 'view', tilt: args.tilt })
      return { ok: true, tilt: args.tilt }
    }
    case 'reset': {
      hub.emit({ op: 'reset' })
      return { ok: true }
    }
    default:
      throw new Error(`未知动作：${String((args as { action?: unknown }).action)}`)
  }
}
