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
  entry_count: number
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
  let degraded = false
  for (const e of entries) {
    const d = e.data ?? {}
    switch (e.kind) {
      case 'alert':
        alerts.set(e.event_id, e)
        if (status === 'stable') status = 'monitoring'
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
