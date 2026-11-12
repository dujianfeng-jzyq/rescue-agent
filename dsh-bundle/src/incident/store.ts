/**
 * 事件库（第三版 P3，docs/design-v3.md 7.2）：与 Python 侧 rescue_contract/incidents.py 格式和折叠规则相同。
 * 一个事件一个 <incident_id>.jsonl，只追加；状态、待确认告警、跟踪计划都是折叠出来的。
 * 写入方：监听器（Python）和本插件；共用 tests/fixtures/incidents 的样例保证两边折叠结果一致。
 */

import { randomBytes } from 'node:crypto'
import { constants } from 'node:fs'
import { mkdir, open, readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'

export const STATUSES = ['monitoring', 'handling', 'stable', 'closed'] as const
export type Status = (typeof STATUSES)[number]
export const STATUS_NAMES: Record<Status, string> = { monitoring: '监测中', handling: '处置中', stable: '已稳定', closed: '已关闭' }
export const KINDS = [
  'opened', 'alert', 'recovered', 'degraded', 'delivered', 'delivery_failed', 'acked', 'status',
  'followup_added', 'followup_done', 'followup_due', 'note',
] as const // prettier-ignore
export type Kind = (typeof KINDS)[number]
export const CATEGORIES = ['fire', 'monitor'] as const

export interface Entry {
  event_id: string
  at: string
  wall: string
  kind: Kind
  by: string
  data: Record<string, any>
}

export interface Followup {
  followup_id: string
  due_at: string
  expect?: string
  added_at: string
  state: 'pending' | 'due' | 'done'
  [k: string]: unknown
}

export interface IncidentView {
  incident_id: string
  category: string
  panel: string | null
  status: Status
  status_name: string
  opened_at: string
  updated_at: string
  summary: string
  degraded: boolean
  pending_alerts: string[]
  followups: Followup[]
  /** 等人工确认、还没有结果的对外动作（approval_pending 没有 approval_decided） */
  pending_approvals: PendingApproval[]
  entry_count: number
}

export interface PendingApproval {
  approval_key: string
  tool: string
  asked_wall: string
  escalated: boolean
  [k: string]: unknown
}

export class IncidentNotFound extends Error {}

const ID_CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789'
const rand = (n = 6) => Array.from(randomBytes(n), (b) => ID_CHARS[b % ID_CHARS.length]).join('')

export function mineStamp(at: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(at)) throw new Error(`矿井时间格式不对：${at}`)
  return at.slice(0, 19).replace(/[-:]/g, '')
}

export function fold(incidentId: string, entries: Entry[]): IncidentView {
  if (entries.length === 0 || entries[0]!.kind !== 'opened') throw new Error(`事件 ${incidentId} 的第一条应为 opened`)
  const first = entries[0]!.data
  let status: Status = 'monitoring'
  const alerts = new Map<string, Entry>()
  const handled = new Set<string>()
  const followups = new Map<string, Followup>()
  const approvals = new Map<string, PendingApproval>()
  let degraded = false
  for (const e of entries) {
    const d = e.data ?? {}
    switch (e.kind) {
      case 'alert':
        alerts.set(e.event_id, e)
        if (status === 'stable' && d.cause !== 'followup_due') status = 'monitoring' // 稳定后再次告警回到监测中；计划内的跟踪到点不算
        break
      case 'delivered':
      case 'acked':
        handled.add(d.alert_id)
        break
      case 'status':
        if (!STATUSES.includes(d.status)) throw new Error(`事件 ${incidentId}：未知状态 ${d.status}`)
        status = d.status
        break
      case 'followup_added':
        followups.set(d.followup_id, { ...d, added_at: e.at, state: 'pending' } as Followup)
        break
      case 'followup_due':
        if (followups.has(d.followup_id)) followups.get(d.followup_id)!.state = 'due'
        break
      case 'followup_done':
        if (followups.has(d.followup_id)) followups.get(d.followup_id)!.state = 'done'
        break
      case 'note':
        if (d.type === 'approval_pending') approvals.set(d.approval_key, { ...d, asked_wall: e.wall, escalated: false } as PendingApproval)
        else if (d.type === 'approval_decided') approvals.delete(d.approval_key)
        else if (d.type === 'approval_escalated' && approvals.has(d.approval_key)) approvals.get(d.approval_key)!.escalated = true
        break
      case 'degraded':
        degraded = true
        break
      case 'recovered':
        if (d.of === 'degraded') degraded = false
        break
    }
  }
  const last = entries[entries.length - 1]!
  return {
    incident_id: incidentId,
    category: first.category,
    panel: first.panel ?? null,
    status,
    status_name: STATUS_NAMES[status],
    opened_at: entries[0]!.at,
    updated_at: last.at,
    summary: first.summary ?? '',
    degraded,
    pending_alerts: [...alerts.keys()].filter((id) => !handled.has(id)),
    followups: [...followups.values()],
    pending_approvals: [...approvals.values()],
    entry_count: entries.length,
  }
}

export class IncidentStore {
  readonly dir: string
  constructor(dir: string) {
    this.dir = dir
  }

  private path(id: string): string {
    if (!id.startsWith('INC-') || id.includes('/') || id.includes('\\') || id.includes('..')) throw new IncidentNotFound(`事件编号不合法：${id}`)
    return join(this.dir, `${id}.jsonl`)
  }

  private line(at: string, kind: Kind, by: string, data: Record<string, unknown>): { text: string; entry: Entry } {
    if (!KINDS.includes(kind)) throw new Error(`未知的条目类型 ${kind}`)
    const entry: Entry = {
      event_id: `EVT-${mineStamp(at)}-${rand()}`,
      at: at.slice(0, 19),
      wall: new Date().toISOString().slice(0, 19) + '+00:00',
      kind,
      by,
      data,
    }
    return { text: JSON.stringify(entry) + '\n', entry }
  }

  async open(at: string, o: { category: string; panel: string | null; summary: string; by?: string }): Promise<string> {
    if (!(CATEGORIES as readonly string[]).includes(o.category)) throw new Error(`未知的事件类别 ${o.category}`)
    await mkdir(this.dir, { recursive: true })
    const { text } = this.line(at, 'opened', o.by ?? 'agent', { category: o.category, panel: o.panel, summary: o.summary })
    for (let i = 0; i < 5; i++) {
      const id = `INC-${mineStamp(at)}-${rand()}`
      try {
        const fh = await open(this.path(id), constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL, 0o644)
        try {
          await fh.writeFile(text, 'utf8')
        } finally {
          await fh.close()
        }
        return id
      } catch (err) {
        if ((err as NodeJS.ErrnoException).code !== 'EEXIST') throw err
      }
    }
    throw new Error('生成事件编号失败')
  }

  async append(id: string, at: string, kind: Kind, by: string, data: Record<string, unknown> = {}): Promise<Entry> {
    const path = this.path(id)
    const { text, entry } = this.line(at, kind, by, data)
    let fh
    try {
      fh = await open(path, constants.O_WRONLY | constants.O_APPEND)
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === 'ENOENT') throw new IncidentNotFound(`找不到事件 ${id}`)
      throw err
    }
    try {
      await fh.write(text) // 一次写入一整行
    } finally {
      await fh.close()
    }
    return entry
  }

  async entries(id: string): Promise<Entry[]> {
    let raw: string
    try {
      raw = await readFile(this.path(id), 'utf8')
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === 'ENOENT') throw new IncidentNotFound(`找不到事件 ${id}`)
      throw err
    }
    const out: Entry[] = []
    for (const line of raw.split('\n')) {
      if (!line.trim()) continue
      try {
        out.push(JSON.parse(line))
      } catch {
        // 写到一半崩溃留下的残行：跳过
      }
    }
    return out
  }

  async get(id: string): Promise<IncidentView> {
    return fold(id, await this.entries(id))
  }

  async ids(): Promise<string[]> {
    try {
      return (await readdir(this.dir)).filter((f) => f.startsWith('INC-') && f.endsWith('.jsonl')).map((f) => f.slice(0, -6)).sort()
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === 'ENOENT') return []
      throw err
    }
  }

  async list(includeClosed = false): Promise<IncidentView[]> {
    const out: IncidentView[] = []
    for (const id of await this.ids()) {
      try {
        const v = await this.get(id)
        if (includeClosed || v.status !== 'closed') out.push(v)
      } catch {
        // 坏的事件文件不挡住其他事件
      }
    }
    return out
  }

  async findOpen(category: string, panel: string | null): Promise<IncidentView | undefined> {
    const found = (await this.list()).filter((v) => v.category === category && v.panel === panel)
    return found.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1))[0]
  }
}

/**
 * 研判、复评、方案、对外动作等“处理告警”的记录类型（流程和 guard 自动写进时间线的 note）；acked 也算已处理。
 * note 的 as_of 是它所依据记录的取数时间（矿井时间，多条记录取最早的），由写 note 的代码从记录里取。
 */
const HANDLING_NOTES: Record<string, string> = {
  assessment: '研判',
  effect_review: '效果复评',
  firefighting_plan: '防灭火方案',
  sealing_plan: '封闭方案',
  unsealing: '启封评估',
  action: '对外动作',
}

export interface AlertStatus {
  alert_id: string
  at: string
  cause: string
  summary: string
  /** 已投递次数（delivered 条目数）；大于 1 一定是重复投递 */
  deliveries: number
  /** handled：这条告警之后时间线里已有依据不早于告警的研判等处理记录（或被确认过）；new：还没有 */
  state: 'handled' | 'new'
  /** 已处理时，依据是哪类记录（如“研判”） */
  handled_by: string | null
}

/** note 依据的数据时间；没有（旧条目）返回 null，按“不能证明覆盖了告警”处理 */
function noteAsOf(e: Entry): string | null {
  const v = e.data.as_of
  return typeof v === 'string' && v ? v.slice(0, 19) : null
}

/**
 * 每条告警是不是已经处理过——由代码按时间线判断，不靠智能体自己读（第五版：重复投递的告警应忽略，评测里智能体约三分之二照消息里的
 * “立即研判”重做）。告警条目之后（文件顺序）出现针对它的 acked 一律算已处理；此外**按告警原因**看之后的记录（coveredBy）：
 * - 异常（anomaly）、矿井报警（mine_alarm）：研判、复评、方案、对外动作的记录，**且该记录依据的数据不早于告警**（as_of ≥ 告警的矿井时间）。
 *   只看文件顺序不够：智能体忙着处理上一条告警时新告警进来，上一轮用旧数据写的研判排在新告警之后，会把一次真正的升级误判成“已处理”
 *   （2026-11-10 演示：19:32 的蓝色研判盖住了 20:02 的黄色告警）。没有 as_of 的记录不算（fail closed）。
 * - 跟踪到点（followup_due）：只有之后的效果复评（数据不早于告警），或这条跟踪计划已完成、被新计划取代（followup_done）。
 *   研判不算：原来不分原因，到点时智能体正好用新数据做了研判，复评告警就被记为已处理、复评被跳过，跟踪计划停在“到点”再没人提醒
 *   （2026-11-11 代码复查第 3 轮）。
 * - 确认超时（approval_timeout）：只有这条确认有了结果（同一 approval_key 的 approval_decided）。
 * - 监听降级（degraded）和不认识的原因：只有 acked。
 * 只用事件库已有的条目，不改折叠规则（Python 侧共用折叠样例不受影响）。
 */
export function alertStatuses(entries: Entry[], only?: Set<string>): AlertStatus[] {
  const out: AlertStatus[] = []
  entries.forEach((e, i) => {
    if (e.kind !== 'alert' || (only && !only.has(e.event_id))) return
    let by: string | null = null
    for (const later of entries.slice(i + 1)) {
      if (later.kind === 'acked' && later.data.alert_id === e.event_id) {
        by = typeof later.data.handled_by === 'string' && later.data.handled_by ? later.data.handled_by : '确认'
        break
      }
      by = coveredBy(e, later)
      if (by) break
    }
    out.push({
      alert_id: e.event_id,
      at: e.at,
      cause: String(e.data.cause ?? ''),
      summary: String(e.data.summary ?? ''),
      deliveries: entries.filter((x) => x.kind === 'delivered' && x.data.alert_id === e.event_id).length,
      state: by ? 'handled' : 'new',
      handled_by: by,
    })
  })
  return out
}

/** 告警之后的一条记录是否按告警原因处理了它（acked 之外）；是就返回依据的名称 */
function coveredBy(alert: Entry, later: Entry): string | null {
  const alertAt = alert.at.slice(0, 19)
  const t = later.kind === 'note' ? String(later.data.type ?? '') : ''
  const asOf = t ? noteAsOf(later) : null
  const fresh = (type: string) => t === type && asOf !== null && asOf >= alertAt
  switch (String(alert.data.cause ?? '')) {
    case 'anomaly':
    case 'mine_alarm':
      return HANDLING_NOTES[t] && fresh(t) ? HANDLING_NOTES[t]! : null
    case 'followup_due':
      if (fresh('effect_review')) return HANDLING_NOTES.effect_review!
      if (later.kind === 'followup_done' && alert.data.followup_id && later.data.followup_id === alert.data.followup_id) return '跟踪计划已完成或被取代'
      return null
    case 'approval_timeout':
      return t === 'approval_decided' && alert.data.approval_key && later.data.approval_key === alert.data.approval_key ? '确认已有结果' : null
    default:
      return null
  }
}
