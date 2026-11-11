/**
 * 按技能的采集清单（references/collect.yaml）从 mine_data 取数，整理成证据内容。
 *
 * Python 侧 rescue_contract.collect 实现同一规则（不经 DSH 的基线评测用），两边对同一时刻的模拟矿井采集，
 * 证据内容的规范化 JSON 必须逐字节相同（一致性测试 mcp/contract/tests/test_collect_parity.py）。所以这里：
 * - 测点按 id 排序，读数按 (sensor_id, measurand, ts) 排序；
 * - 时间截到秒；窗口起点 = as_of − window_h；
 * - 只用 mine_data 的工具（CLAUDE.md 约定 5），工具返回的结构化数据原样保留。
 *
 * 清单格式见 skills/README.md“采集清单”。
 */

import { parse as parseYaml } from 'yaml'

import { mineTimeToSeconds } from './records.ts'

export interface SensorGroupSpec {
  name: string
  select: { role?: string; source?: string; panel?: boolean }
  /** 测点必须同时具备的参数 */
  require_measurands?: string[]
  /** 取哪些参数的读数；缺省为测点的全部参数 */
  measurands?: string[]
  /** 历史窗口（矿井小时）；0 或缺省只取最新值 */
  window_h?: number
  /** 没有匹配的测点时记为缺失 */
  required?: boolean
  /** 窗口按处置开始时间取：从 since 之前这么多小时到现在（复评用；采集时必须给 since） */
  before_since_h?: number
}

export interface CollectKindSpec {
  sensors?: SensorGroupSpec[]
  events_h?: number
  /** 矿井报警平台未恢复报警的回看时长（矿井小时） */
  mine_alarms_h?: number
  fire_areas?: boolean
  personnel?: boolean
  inventory?: boolean
  rescue_teams?: boolean
  network?: boolean
}

export interface CollectSpec {
  status: 'placeholder' | 'reviewed'
  reviewed_by?: string | null
  kinds: Record<string, CollectKindSpec>
}

export type CallTool = (tool: string, args: Record<string, unknown>) => Promise<any>

const SELECT_KEYS = ['role', 'source', 'panel']

/** select 只认 role（语义角色）、source（测点来源）、panel（true：只取本工作面的测点）；不再按测点编号取数。与 Python 侧同一规则。 */
function checkSelect(sel: Record<string, unknown>, where: string): void {
  if ('ids' in sel) throw new Error(`${where}：select 不再支持 ids，请改用 role（按语义角色取数）`)
  const unknown = Object.keys(sel).filter((k) => !SELECT_KEYS.includes(k)).sort()
  if (unknown.length) throw new Error(`${where}：select 里有不认识的键 [${unknown.join(', ')}]（只认 [${SELECT_KEYS.join(', ')}]）`)
  if (!sel.role && !sel.source) throw new Error(`${where}：select 至少要有 role 或 source`)
}

export function parseCollectSpec(text: string, where = 'collect.yaml'): CollectSpec {
  const spec = parseYaml(text)
  if (!spec || typeof spec !== 'object') throw new Error(`${where}：不是映射`)
  if (spec.status !== 'placeholder' && spec.status !== 'reviewed') throw new Error(`${where}：顶层要有 status: placeholder 或 reviewed`)
  if (!spec.kinds || typeof spec.kinds !== 'object') throw new Error(`${where}：缺少 kinds`)
  for (const [kind, k] of Object.entries<any>(spec.kinds)) {
    for (const g of k.sensors ?? []) {
      if (!g.name || !g.select || typeof g.select !== 'object' || Array.isArray(g.select)) throw new Error(`${where}：${kind} 的测点组要有 name 和 select`)
      checkSelect(g.select, `${where}：${kind} 的测点组 ${g.name}`)
    }
  }
  return spec as CollectSpec
}

/** MCP 对返回列表的工具包一层 {result: [...]} */
function unwrap(v: any): any {
  if (v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 1 && Array.isArray(v.result)) return v.result
  return v
}

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0)

function sortReadings(rs: any[]): any[] {
  return [...rs].sort((a, b) => cmp(a.sensor_id, b.sensor_id) || cmp(a.measurand, b.measurand) || cmp(a.ts, b.ts))
}

/** 矿井时间减去若干小时，截到秒 */
export function minusHours(asOf: string, hours: number): string {
  const t = Date.parse(`${mineTimeToSeconds(asOf)}Z`) - Math.round(hours * 3600_000)
  return new Date(t).toISOString().slice(0, 19)
}

function selectSensors(all: any[], g: SensorGroupSpec, panel: string): any[] {
  return all
    .filter((s) => (g.select.role ? s.role === g.select.role : true))
    .filter((s) => (g.select.source ? s.source === g.select.source : true))
    .filter((s) => (g.select.panel ? s.panel === panel : true))
    .filter((s) => (g.require_measurands ?? []).every((m) => s.measurands.includes(m)))
    .sort((a, b) => cmp(a.id, b.id))
}

export interface Evidence {
  kind: string
  panel: string
  as_of: string
  since?: string
  groups: Record<string, { sensors: any[]; measurands: Record<string, string[]>; latest: any[]; readings: any[]; window_h: number }>
  missing: { group: string; reason: string }[]
  events?: any[]
  mine_alarms?: any[] | null
  fire_areas?: any[]
  personnel?: any
  inventory?: any[]
  rescue_teams?: any[]
  network?: any
}

/** 从 since 之前 beforeH 小时到 asOf 的窗口长度（小时）。与 Python 侧同一算法：整秒差 / 3600 + beforeH */
export function sinceWindowH(asOf: string, since: string, beforeH: number): number {
  const secs = Math.trunc((Date.parse(`${asOf}Z`) - Date.parse(`${since}Z`)) / 1000)
  if (secs < 0) throw new Error(`处置开始时间 ${since} 晚于矿井当前时间 ${asOf}`)
  return secs / 3600 + beforeH
}

export async function collect(call: CallTool, spec: CollectSpec, kind: string, panel: string, opts: { since?: string } = {}): Promise<Evidence> {
  const k = spec.kinds[kind]
  if (!k) throw new Error(`采集清单没有 ${kind}（有：${Object.keys(spec.kinds).join('、')}）`)
  const asOf = mineTimeToSeconds(String((await call('get_clock', {})).now))
  const ev: Evidence = { kind, panel, as_of: asOf, groups: {}, missing: [] }
  const since = opts.since !== undefined ? mineTimeToSeconds(opts.since) : undefined
  if (since !== undefined) ev.since = since
  const sensors = k.sensors?.length ? unwrap(await call('list_sensors', {})) : []
  for (const g of k.sensors ?? []) {
    const picked = selectSensors(sensors, g, panel)
    let windowH = g.window_h ?? 0
    if (g.before_since_h !== undefined) {
      if (since === undefined) throw new Error(`采集清单 ${kind} 的测点组 ${g.name} 按处置开始时间取窗口，需要给出 since`)
      windowH = sinceWindowH(asOf, since, g.before_since_h)
    }
    const measurands: Record<string, string[]> = {}
    for (const s of picked) measurands[s.id] = (g.measurands ? s.measurands.filter((m: string) => g.measurands!.includes(m)) : [...s.measurands]).sort(cmp)
    const ids = picked.map((s) => s.id)
    let latest: any[] = []
    let readings: any[] = []
    if (ids.length) {
      latest = unwrap(await call('get_latest', { sensor_ids: ids })).filter((r: any) => measurands[r.sensor_id]?.includes(r.measurand))
      if (windowH > 0) {
        const wanted = [...new Set(Object.values(measurands).flat())].sort(cmp)
        readings = unwrap(await call('get_readings', { sensor_ids: ids, start: minusHours(asOf, windowH), end: asOf, measurands: wanted })).filter(
          (r: any) => measurands[r.sensor_id]?.includes(r.measurand),
        )
      }
    } else if (g.required) {
      ev.missing.push({ group: g.name, reason: '没有匹配的测点' })
    }
    ev.groups[g.name] = { sensors: picked, measurands, latest: sortReadings(latest), readings: sortReadings(readings), window_h: windowH }
  }
  if (k.events_h) ev.events = unwrap(await call('get_events', { hours: k.events_h }))
  if (k.mine_alarms_h) {
    // 矿井报警平台的未恢复报警（可选功能：矿井没有报警平台时为 null，不影响研判）
    try {
      ev.mine_alarms = unwrap(await call('get_alarms', { hours: k.mine_alarms_h, active_only: true }))
    } catch {
      ev.mine_alarms = null
    }
  }
  if (k.fire_areas) ev.fire_areas = unwrap(await call('get_fire_areas', {}))
  if (k.personnel) ev.personnel = await call('get_personnel', {})
  if (k.inventory) ev.inventory = unwrap(await call('get_inventory', {}))
  if (k.rescue_teams) ev.rescue_teams = unwrap(await call('get_rescue_teams', {}))
  if (k.network) ev.network = await call('get_network', {})
  return ev
}
