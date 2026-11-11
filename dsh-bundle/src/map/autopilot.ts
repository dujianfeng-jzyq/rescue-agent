/**
 * 地图自动叠加（docs/design-v4-map.md P2）：不经模型，由代码把矿井状态画到图上。
 *   - 事件所在工作面：按预警等级着色，标签写“等级 · 阶段”（来自监听器写进告警的检测摘要）；
 *   - 矿井自己报警平台里仍在越限的测点：标在测点所在的位置，标签写读数（矿井原值）；
 *   - 火区台账里的火区：标“火区已封闭/已启封”；
 *   - 来了新告警：镜头自动飞到出事的工作面。
 * 标记编号都以 auto: 开头，智能体手动标的（别的编号）不受影响。一个位置最多一个标记，多个来源合并成一条标签；
 * 颜色：位置上有事件的预警等级就用它，否则取各来源里最严重的。
 *
 * 只显示，不判断：等级、阶段、读数、报警都是别处算好的，这里不改、不推断、不补。
 */

import type { MapCommand, MapFigure, MapMark } from './hub.ts'
import { anchorForPanel } from './mapping.ts'
import type { MineMap } from './mapping.ts'
import type { MapState } from './state.ts'

export const AUTO_PREFIX = 'auto:'
/** 智能体 map_view 标记的编号前缀（定义在这里免得和 tools.ts 循环引用）。 */
export const AGENT_PREFIX = 'agent:'
const SEVERITY = ['ok', 'info', 'blue', 'yellow', 'warn', 'orange', 'alarm', 'red']
const MAX_TEXT = 60
const MAX_ALARM_PARTS = 2

export interface AutoMark {
  id: string
  name: string
  level: string
  text: string
}

const sev = (level: string): number => Math.max(0, SEVERITY.indexOf(level))

function fmt(v: number | null): string {
  if (v === null) return ''
  return Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2)
}

/** 预警等级 → 标记等级（none = 没有预警，画成“正常”绿色；没有研判过的画成提示蓝色）。 */
export function levelOf(level: string | null): string {
  if (level === null) return 'info'
  if (level === 'none') return 'ok'
  return ['blue', 'yellow', 'orange', 'red'].includes(level) ? level : 'info'
}

interface Part {
  level: string
  text: string
  /** 事件的预警等级：同一个位置有它时，标记颜色就是预警等级（报警写在文字里），颜色的含义才一致 */
  primary?: boolean
}

export function computeOverlay(state: MapState, map: MineMap): AutoMark[] {
  const byAnchor = new Map<string, Part[]>()
  const add = (anchor: string | null, part: Part): void => {
    if (!anchor) return
    const list = byAnchor.get(anchor)
    if (list) list.push(part)
    else byAnchor.set(anchor, [part])
  }

  const inc = state.incident
  if (inc) {
    // 等级 none 的名称是“无”，单独一个字有歧义，写成“无预警”
    const levelName = inc.level === 'none' ? '无预警' : inc.levelName
    const text = [levelName, inc.stageName].filter(Boolean).join(' · ') || inc.statusName
    add(anchorForPanel(map, inc.panel), { level: levelOf(inc.level), text, primary: true })
  }

  // 同一个位置的多条报警合并：最多列两条，其余写“等 N 项”
  const alarmsAt = new Map<string, MapState['alarms']['items']>()
  for (const a of state.alarms.items) {
    if (!a.anchor) continue
    const l = alarmsAt.get(a.anchor)
    if (l) l.push(a)
    else alarmsAt.set(a.anchor, [a])
  }
  for (const [anchor, items] of alarmsAt) {
    // 报警记录里的值是越限那一刻的读数，不是当前读数，所以写“越限”
    const shown = items.slice(0, MAX_ALARM_PARTS).map((a) => `${a.measurand ?? ''} 越限 ${fmt(a.value)}${a.value === null ? '' : a.unit}`.trim())
    const more = items.length > MAX_ALARM_PARTS ? `等${items.length}项` : ''
    const worst = items.some((a) => a.level === 'critical' || a.level === 'alarm') ? 'alarm' : 'warn'
    add(anchor, { level: worst, text: `${shown.join('、')}${more}` })
  }

  for (const f of state.fireAreas.items) add(f.anchor, { level: 'info', text: f.status === 'sealed' ? '火区已封闭' : '火区已启封' })

  const out: AutoMark[] = []
  for (const [anchor, parts] of byAnchor) {
    const primary = parts.find((p) => p.primary)
    const level = primary ? primary.level : parts.reduce((m, p) => (sev(p.level) > sev(m) ? p.level : m), 'ok')
    out.push({ id: `${AUTO_PREFIX}${anchor}`, name: anchor, level, text: parts.map((p) => p.text).join(' | ').slice(0, MAX_TEXT) })
  }
  return out.sort((a, b) => (a.id < b.id ? -1 : 1))
}

/** 把想要的自动标记和当前已有的对账，只返回需要发的指令（没变化的不发，不制造多余的广播）。 */
export function reconcile(current: MapMark[], desired: AutoMark[]): MapCommand[] {
  const cmds: MapCommand[] = []
  const have = new Map(current.filter((m) => m.id.startsWith(AUTO_PREFIX)).map((m) => [m.id, m]))
  for (const d of desired) {
    const c = have.get(d.id)
    if (!c || c.name !== d.name || c.level !== d.level || c.text !== d.text) cmds.push({ op: 'mark', id: d.id, name: d.name, level: d.level, text: d.text })
    have.delete(d.id)
  }
  for (const id of have.keys()) cmds.push({ op: 'unmark', id })
  // 系统标的位置上不留智能体的标记：后者的等级取自当时的记录，不会更新，两个圈叠在一起颜色不一致（如黄色预警时留着蓝色）
  const autoNames = new Set(desired.map((d) => d.name))
  for (const m of current) if (m.id.startsWith(AGENT_PREFIX) && autoNames.has(m.name)) cmds.push({ op: 'unmark', id: m.id })
  return cmds
}

/** 图元对账（撤人路线、污染巷道、人数）：新增、变化的发 figure，不再需要的发 unfigure；没变化的不发。 */
export function reconcileFigures(current: MapFigure[], desired: MapFigure[]): MapCommand[] {
  const cmds: MapCommand[] = []
  const have = new Map(current.filter((f) => f.id.startsWith(AUTO_PREFIX)).map((f) => [f.id, f]))
  for (const d of desired) {
    const c = have.get(d.id)
    if (!c || JSON.stringify(c) !== JSON.stringify(d)) cmds.push({ op: 'figure', fig: d })
    have.delete(d.id)
  }
  for (const id of have.keys()) cmds.push({ op: 'unfigure', fig: { id, type: 'line', kind: '' } })
  return cmds
}

/**
 * 自动驾驶：每个周期给一份状态，返回要执行的地图指令。
 * 第一次只记下“已有的告警”，不飞（刚打开或重启时镜头不该乱动）；之后最近一条告警的编号变了才飞到该事件所在的工作面。
 */
export class Autopilot {
  private lastAlert: string | null | undefined = undefined
  private readonly zoom: number

  constructor(zoom = 3) {
    this.zoom = zoom
  }

  step(state: MapState, map: MineMap, currentMarks: MapMark[], currentFigures: MapFigure[] = []): MapCommand[] {
    const cmds = [...reconcile(currentMarks, computeOverlay(state, map)), ...reconcileFigures(currentFigures, state.evacuation?.figures ?? [])]
    const key = state.incident ? `${state.incident.id}:${state.incident.latestAlertId ?? ''}` : null
    if (this.lastAlert !== undefined && key !== null && key !== this.lastAlert && state.incident?.latestAlertId) {
      const name = anchorForPanel(map, state.incident.panel)
      if (name) cmds.push({ op: 'focus', name, zoom: this.zoom })
    }
    this.lastAlert = key
    return cmds
  }
}
