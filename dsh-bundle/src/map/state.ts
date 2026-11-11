/**
 * 大屏界面的状态汇总（docs/design-v4-map.md 第 6 节）：把事件库、记录目录、监听器心跳、矿井读数整理成一份 JSON，
 * 给浏览器端的顶栏、左栏、右栏显示。
 *
 * 只汇总、不判断：预警等级、阶段来自监听器写进事件库的检测摘要（check），读数是矿井原始值，不在这里算阈值或结论。
 * 取不到的部分给 null 或空表并带上原因，界面如实显示“暂无数据”，不用旧值冒充。
 */

import { readFileSync } from 'node:fs'
import { readdir, readFile, stat } from 'node:fs/promises'
import { join } from 'node:path'

import { IncidentStore } from '../incident/store.ts'
import type { Entry, IncidentView } from '../incident/store.ts'
import type { ActivitySnapshot } from './activity.ts'
import { evacuationView, latestPersonnelRecord } from './evacuation.ts'
import type { EvacuationView, Network } from './evacuation.ts'
import { anchorForPanel, anchorForSensor, EMPTY_MAP } from './mapping.ts'
import type { MineMap } from './mapping.ts'

export interface MineCaller {
  call(tool: string, args: Record<string, unknown>): Promise<any>
}

export interface StateDeps {
  incidentDir: string
  recordDir: string
  /** 矿井数据服务；没有配置时读数和矿井时间为空 */
  mine: MineCaller | null
  /** 读数显示哪个工作面；缺省取当前事件的工作面，再缺省取第一个有测点的工作面 */
  panel?: string | undefined
  /** 地图映射表：测点、火区在地图上的位置；缺省为空（只是不给位置） */
  map?: MineMap
  /** 智能体的实时活动（DSH 事件）；没有时智能体状态只能由事件库推断 */
  activity?: ActivitySnapshot
  /** 矿井网络（get_network）；撤人路线要用，取不到时只列文字、不画 */
  network?: () => Promise<Network | null>
  /** 演示进度（只在演示环境有）；取不到按没有 */
  demoStatus?: () => Promise<DemoStatus | null>
  nowMs?: () => number
}

/** 演示导演的进度：state 为 finished 时大屏显示“演示已结束” */
export interface DemoStatus {
  state: string
  outcome: string
  endedAt: string | null
}

export interface TimelineItem {
  at: string
  kind: string
  label: string
  text: string
}

export interface ReadingRow {
  sensorId: string
  /** 在地图上的位置（映射表给的名称）；没有映射为 null */
  anchor: string | null
  sensor: string
  measurand: string
  value: number | null
  unit: string
  quality: string
  role: string
}

export interface MineAlarmRow {
  id: string
  sensorId: string | null
  sensor: string
  measurand: string | null
  level: string
  value: number | null
  threshold: number | null
  unit: string
  message: string
  raisedAt: string
  anchor: string | null
}

export interface FireAreaRow {
  id: string
  panel: string
  name: string
  status: string
  sealedAt: string
  anchor: string | null
}

export interface MapState {
  wall: string
  /** 演示进度（演示环境）；不是演示或取不到为 null */
  demo?: DemoStatus | null
  /** 映射表里被丢弃的条目（名称不在地图上等）；没有问题时为空 */
  mapWarnings?: string[]
  mine: { now: string | null; ok: boolean; error?: string }
  sentinel: { known: boolean; ageS: number | null; error: string | null }
  /** 当前最需要关注的事件（未关闭里最近更新的火情事件）；没有为 null */
  incident: null | {
    id: string
    panel: string | null
    status: string
    statusName: string
    summary: string
    openedAt: string
    updatedAt: string
    level: string | null
    levelName: string | null
    stage: string | null
    stageName: string | null
    alarmCount: number | null
    checkAsOf: string | null
    /** 最近一条告警的编号：地图据此判断“来了新告警”，自动飞到出事位置 */
    latestAlertId: string | null
    pendingAlerts: number
    degraded: boolean
    followups: { id: string; dueAt: string; expect: string; state: string }[]
    pendingApprovals: { key: string; tool: string; name: string; confirmer: string | null; timeoutMin: number | null; askedWall: string; escalated: boolean }[]
    timeline: TimelineItem[]
  }
  openIncidents: number
  /** 撤人路线与受威胁人员：来自该工作面最近一份 personnel 记录（docs/design-v4-evacuation.md）；没有记录为 null */
  evacuation: EvacuationView | null
  readings: { panel: string | null; rows: ReadingRow[]; error?: string }
  /** 矿井自己报警平台里仍在越限的报警（不是重算的结果）；取不到为空并带原因 */
  alarms: { items: MineAlarmRow[]; error?: string }
  /** 火区台账（矿井人工登记的事实） */
  fireAreas: { items: FireAreaRow[]; error?: string }
  records: { id: string; kind: string; kindName: string; panel: string | null; asOf: string; createdAt: string }[]
  agent: {
    state: 'idle' | 'working' | 'waiting_approval' | 'waiting_question' | 'alert_pending' | 'degraded'
    label: string
    /** 正在做什么（认识的工具的中文说明），没有为空 */
    doing: string[]
    /** live = 来自 DSH 的实时事件；inferred = 只能由事件库推断（没收到过事件） */
    source: 'live' | 'inferred'
  }
  /**
   * 值班会话：监听器的告警投递到这里（编号是唤醒插件记在事件库目录 duty-session.json 里的）。
   * lastDelivery 是当前事件最近一次投递；浏览器看到新的投递就自动切到值班会话（操作员不用自己去会话列表里找）。
   */
  duty: { sessionId: string | null; lastDelivery: { id: string; wall: string; sessionId: string } | null }
}

const UNIT: Record<string, string> = { CO: 'ppm', CO2: '%', CH4: '%', O2: '%', N2: '%', C2H4: 'ppm', C2H2: 'ppm', C2H6: 'ppm', H2: 'ppm', TEMP: '℃', WIND: 'm/s' }
/** 左栏要看的测点角色（占位）：顺序即显示顺序 */
const KEY_ROLES = ['upper_corner', 'return_airway', 'goaf_gas_tube', 'goaf_temperature', 'main_return']
const KIND_NAME: Record<string, string> = { evidence: '证据', result: '研判结果', report: '报告' }
const ENTRY_LABEL: Record<string, string> = {
  opened: '开事件', alert: '告警', recovered: '恢复', degraded: '监听降级', delivered: '已投递', delivery_failed: '投递失败', acked: '已确认',
  status: '状态变化', followup_added: '登记跟踪', followup_done: '跟踪完成', followup_due: '跟踪到点', note: '记录',
}
const NOTE_LABEL: Record<string, string> = {
  assessment: '研判', sealing_plan: '封闭方案', firefighting_plan: '防灭火方案', effect_review: '效果复评', unsealing: '启封评估',
  approval_pending: '等待人工确认', approval_decided: '人工确认', approval_escalated: '确认超时升级', action: '对外动作',
}
/** 时间线里不显示的条目（投递细节对操作员没有信息量） */
const HIDDEN = new Set(['delivered', 'acked'])
/** 动作分级表（rescue_contract.action_policy 导出）：待确认指令的中文名、确认人、批准超时都从这里取 */
interface PolicyRow {
  tool: string
  name: string
  confirmer: string | null
  approval_timeout_min: number | null
}
let policyCache: Map<string, PolicyRow> | undefined
export function policyOf(tool: string): PolicyRow | undefined {
  if (!policyCache) {
    try {
      const raw = JSON.parse(readFileSync(new URL('../../data/action-policy.json', import.meta.url), 'utf8')) as { policies: PolicyRow[] }
      policyCache = new Map(raw.policies.map((p) => [p.tool, p]))
    } catch {
      policyCache = new Map()
    }
  }
  return policyCache.get(tool)
}

const MAX_TIMELINE = 14
const MAX_RECORDS = 6

const OUTCOME_NAME: Record<string, string> = { rejected: '已拒绝', 'allowed-once': '已允许', allowed: '已允许', timeout: '超时未确认' }

function entryText(e: Entry): string {
  const d = e.data ?? {}
  // 人工确认的几条记录没有自己的文字，用动作分级表里的名称和确认人拼出来
  if (e.kind === 'note' && typeof d.type === 'string' && d.type.startsWith('approval_') && typeof d.tool === 'string') {
    const pol = policyOf(d.tool)
    const name = pol?.name ?? d.tool
    if (d.type === 'approval_pending') return pol?.confirmer ? `${name}（确认人：${pol.confirmer}）` : name
    if (d.type === 'approval_decided') return `${name} · ${OUTCOME_NAME[String(d.outcome)] ?? String(d.outcome ?? '')}`
    return name
  }
  const t = String(d.summary ?? d.text ?? d.reason ?? d.expect ?? '')
  return t.length > 80 ? `${t.slice(0, 80)}…` : t
}

export function timelineOf(entries: Entry[]): TimelineItem[] {
  const out: TimelineItem[] = []
  for (const e of entries) {
    if (HIDDEN.has(e.kind)) continue
    const label = e.kind === 'note' ? (NOTE_LABEL[String(e.data?.type)] ?? '记录') : (ENTRY_LABEL[e.kind] ?? e.kind)
    out.push({ at: e.at, kind: e.kind, label, text: entryText(e) })
  }
  return out.slice(-MAX_TIMELINE).reverse() // 最新的在前
}

/** 最近一条带检测摘要（check）的条目：告警或恢复。 */
function lastCheck(entries: Entry[]): Record<string, any> | null {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i]!
    const c = e.data?.check ?? (e.data?.level !== undefined ? e.data : undefined)
    if (c && typeof c === 'object' && ('level' in c || 'level_name' in c)) return c as Record<string, any>
  }
  return null
}

function lastAlertId(entries: Entry[]): string | null {
  for (let i = entries.length - 1; i >= 0; i--) if (entries[i]!.kind === 'alert') return entries[i]!.event_id
  return null
}

/**
 * 选当前最需要关注的事件：有待人工确认指令的优先，其次是系统时间上最近有动静的，最后才比矿井时间。
 * 不能只比矿井时间：演示环境重启、重置后矿井时钟会回到更早，旧事件（矿井时间更晚）会压过刚开的新事件。
 */
async function pickIncident(store: IncidentStore, views: IncidentView[]): Promise<IncidentView | undefined> {
  const fire = views.filter((v) => v.category === 'fire' && v.status !== 'closed')
  const pool = fire.length ? fire : views.filter((v) => v.status !== 'closed')
  const withWall = await Promise.all(
    pool.map(async (v) => {
      const entries = await store.entries(v.incident_id).catch(() => [] as Entry[])
      return { v, wall: entries.length ? entries[entries.length - 1]!.wall : '' }
    }),
  )
  withWall.sort((a, b) => {
    const pa = a.v.pending_approvals.length > 0 ? 1 : 0
    const pb = b.v.pending_approvals.length > 0 ? 1 : 0
    if (pa !== pb) return pb - pa
    if (a.wall !== b.wall) return a.wall < b.wall ? 1 : -1
    return a.v.updated_at < b.v.updated_at ? 1 : -1
  })
  return withWall[0]?.v
}

async function readDutySession(dir: string): Promise<string | null> {
  try {
    const saved = JSON.parse(await readFile(join(dir, 'duty-session.json'), 'utf8')) as { sessionId?: unknown }
    return typeof saved.sessionId === 'string' && saved.sessionId ? saved.sessionId : null
  } catch {
    return null
  }
}

function lastDelivery(entries: Entry[]): MapState['duty']['lastDelivery'] {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i]!
    if (e.kind === 'delivered' && typeof e.data?.session_id === 'string') return { id: e.event_id, wall: e.wall, sessionId: e.data.session_id }
  }
  return null
}

async function readHeartbeat(dir: string, nowMs: number): Promise<MapState['sentinel']> {
  try {
    const hb = JSON.parse(await readFile(join(dir, 'sentinel', 'heartbeat.json'), 'utf8')) as { wall?: number; error?: string | null }
    return { known: true, ageS: typeof hb.wall === 'number' ? Math.max(0, Math.round(nowMs / 1000 - hb.wall)) : null, error: hb.error ?? null }
  } catch {
    return { known: false, ageS: null, error: null }
  }
}

async function latestRecords(dir: string): Promise<MapState['records']> {
  let names: string[]
  try {
    names = (await readdir(dir)).filter((n) => /^(EV|RS|RP)-\d{8}T\d{6}-[a-z0-9]{6}\.json$/.test(n))
  } catch {
    return []
  }
  const withTime = await Promise.all(
    names.map(async (n) => {
      try {
        return { n, t: (await stat(join(dir, n))).mtimeMs }
      } catch {
        return { n, t: 0 }
      }
    }),
  )
  const newest = withTime.sort((a, b) => b.t - a.t).slice(0, MAX_RECORDS)
  const out: MapState['records'] = []
  for (const { n } of newest) {
    try {
      const r = JSON.parse(await readFile(join(dir, n), 'utf8')) as Record<string, any>
      out.push({ id: String(r.record_id), kind: String(r.kind), kindName: KIND_NAME[String(r.kind)] ?? String(r.kind), panel: r.panel ?? null, asOf: String(r.as_of ?? ''), createdAt: String(r.created_at ?? '') })
    } catch {
      /* 坏文件跳过 */
    }
  }
  return out
}

/** MCP 对返回列表的工具包一层 {result: [...]}（与证据插件的 unwrap 相同）。 */
function unwrap(v: any): any[] {
  if (Array.isArray(v)) return v
  if (v && typeof v === 'object' && Array.isArray(v.result)) return v.result
  throw new Error('矿井返回的不是列表')
}

function keyReadings(sensors: any[], latestOf: (ids: string[]) => Promise<any[]>, panelHint: string | null | undefined, map: MineMap): Promise<MapState['readings']> {
  return (async () => {
    const panel = panelHint ?? sensors.find((s) => s.panel)?.panel ?? null
    const chosen: any[] = []
    for (const role of KEY_ROLES) for (const s of sensors) if (s.role === role && (panel === null || s.panel === panel || s.panel == null)) chosen.push(s)
    if (chosen.length === 0) return { panel, rows: [], error: '没有找到关键测点' }
    const latest = await latestOf(chosen.map((s) => s.id))
    const bySensor = new Map<string, any[]>()
    for (const r of latest) (bySensor.get(r.sensor_id) ?? bySensor.set(r.sensor_id, []).get(r.sensor_id)!).push(r)
    const rows: ReadingRow[] = []
    for (const s of chosen) {
      const anchor = anchorForSensor(map, s)
      for (const r of bySensor.get(s.id) ?? []) {
        rows.push({ sensorId: s.id, anchor, sensor: s.name, measurand: r.measurand, value: r.value ?? null, unit: UNIT[r.measurand] ?? '', quality: r.quality ?? 'ok', role: s.role })
      }
    }
    return { panel, rows: rows.slice(0, 12) }
  })()
}

export async function buildState(deps: StateDeps): Promise<MapState> {
  const nowMs = (deps.nowMs ?? Date.now)()
  const store = new IncidentStore(deps.incidentDir)
  const views = await store.list(false).catch(() => [] as IncidentView[])
  const main = await pickIncident(store, views)

  let incident: MapState['incident'] = null
  let delivery: MapState['duty']['lastDelivery'] = null
  if (main) {
    const entries = await store.entries(main.incident_id).catch(() => [] as Entry[])
    delivery = lastDelivery(entries)
    const chk = lastCheck(entries)
    incident = {
      id: main.incident_id,
      panel: main.panel,
      status: main.status,
      statusName: main.status_name,
      summary: main.summary,
      openedAt: main.opened_at,
      updatedAt: main.updated_at,
      level: chk?.level ?? null,
      levelName: chk?.level_name ?? null,
      stage: chk?.stage ?? null,
      stageName: chk?.stage_name ?? null,
      alarmCount: typeof chk?.alarm_count === 'number' ? chk.alarm_count : null,
      checkAsOf: chk?.as_of ?? null,
      latestAlertId: lastAlertId(entries),
      pendingAlerts: main.pending_alerts.length,
      degraded: main.degraded,
      followups: main.followups.filter((f) => f.state !== 'done').map((f) => ({ id: f.followup_id, dueAt: f.due_at, expect: String(f.expect ?? ''), state: f.state })),
      pendingApprovals: main.pending_approvals.map((p) => {
        const pol = policyOf(p.tool)
        return { key: p.approval_key, tool: p.tool, name: pol?.name ?? p.tool, confirmer: pol?.confirmer ?? null, timeoutMin: pol?.approval_timeout_min ?? null, askedWall: p.asked_wall, escalated: p.escalated }
      }),
      timeline: timelineOf(entries),
    }
  }

  const map = deps.map ?? EMPTY_MAP
  const mine: MapState['mine'] = { now: null, ok: false }
  let readings: MapState['readings'] = { panel: null, rows: [], error: '矿井数据服务没有配置' }
  let alarms: MapState['alarms'] = { items: [], error: '矿井数据服务没有配置' }
  let fireAreas: MapState['fireAreas'] = { items: [], error: '矿井数据服务没有配置' }
  if (deps.mine) {
    const m = deps.mine
    try {
      mine.now = String((await m.call('get_clock', {})).now)
      mine.ok = true
    } catch (e) {
      mine.error = (e as Error).message
    }
    let sensors: any[] = []
    try {
      sensors = unwrap(await m.call('list_sensors', {}))
      readings = await keyReadings(sensors, async (ids) => unwrap(await m.call('get_latest', { sensor_ids: ids })), deps.panel ?? incident?.panel, map)
    } catch (e) {
      readings = { panel: null, rows: [], error: `读数取不到：${(e as Error).message}` }
    }
    const byId = new Map(sensors.map((s) => [String(s.id), s]))
    try {
      const raw = unwrap(await m.call('get_alarms', { hours: 24, active_only: true }))
      alarms = {
        items: raw.slice(0, 20).map((a) => {
          const s = a.sensor_id ? byId.get(String(a.sensor_id)) : undefined
          return {
            id: String(a.alarm_id),
            sensorId: a.sensor_id ?? null,
            sensor: s?.name ?? String(a.sensor_id ?? '—'),
            measurand: a.measurand ?? null,
            level: String(a.level),
            value: a.value ?? null,
            threshold: a.threshold ?? null,
            unit: UNIT[String(a.measurand)] ?? '',
            message: String(a.message ?? ''),
            raisedAt: String(a.raised_at ?? ''),
            anchor: s ? anchorForSensor(map, s) : null,
          }
        }),
      }
    } catch (e) {
      alarms = { items: [], error: `报警取不到：${(e as Error).message}` }
    }
    try {
      const raw = unwrap(await m.call('get_fire_areas', {}))
      fireAreas = {
        items: raw.map((f) => ({ id: String(f.area_id), panel: String(f.panel), name: String(f.name), status: String(f.status), sealedAt: String(f.sealed_at ?? ''), anchor: anchorForPanel(map, String(f.panel), true) })),
      }
    } catch (e) {
      fireAreas = { items: [], error: `火区台账取不到：${(e as Error).message}` }
    }
    // 火区已封闭：密闭内的安全监控测点断电、读不到数是预期的，显示“封闭断电”而不是缺数（束管、测温缺数照常显示缺数）
    const sealed = new Set(fireAreas.items.filter((f) => f.status === 'sealed').map((f) => f.panel))
    if (readings.panel && sealed.has(readings.panel)) {
      readings.rows = readings.rows.map((r) => (r.quality === 'missing' && byId.get(r.sensorId)?.source === 'safety_monitor' ? { ...r, quality: 'sealed_off' } : r))
    }
  }

  let evacuation: EvacuationView | null = null
  if (incident?.panel) {
    try {
      const rec = await latestPersonnelRecord(deps.recordDir, incident.panel, mine.now)
      if (rec) {
        const computed = !!(rec.content?.result as { computed?: boolean } | undefined)?.computed
        const net = computed && deps.network ? await deps.network().catch(() => null) : null
        evacuation = evacuationView(rec, net, map, mine.now)
      }
    } catch {
      evacuation = null // 记录读不了：不显示，不影响其他部分
    }
  }

  const act = deps.activity
  const live = !!act?.known
  const pendingApprovals = Math.max(incident?.pendingApprovals.length ?? 0, act?.waitingApprovals.length ?? 0)
  let agent: MapState['agent'] = { state: 'idle', label: '待命', doing: [], source: live ? 'live' : 'inferred' }
  if (pendingApprovals > 0) agent = { ...agent, state: 'waiting_approval', label: `等待人工确认（${pendingApprovals} 项）` }
  else if ((act?.waitingQuestions ?? 0) > 0) agent = { ...agent, state: 'waiting_question', label: '等待操作员回答' }
  else if ((act?.running ?? 0) > 0) agent = { ...agent, state: 'working', label: act!.doing.length ? `工作中 · ${act!.doing.join('、')}` : '工作中', doing: act!.doing }
  else if (incident?.degraded) agent = { ...agent, state: 'degraded', label: '监听降级' }
  else if (incident && incident.pendingAlerts > 0) agent = { ...agent, state: 'alert_pending', label: `有 ${incident.pendingAlerts} 条告警待处理` }
  else if (!live && incident && incident.status === 'handling') agent = { ...agent, state: 'working', label: '处置中' } // 没有实时事件时的推断

  const demo = deps.demoStatus ? await deps.demoStatus().catch(() => null) : null
  return {
    wall: new Date(nowMs).toISOString(),
    demo,
    mine,
    sentinel: await readHeartbeat(deps.incidentDir, nowMs),
    incident,
    openIncidents: views.length,
    evacuation,
    readings,
    alarms,
    fireAreas,
    records: await latestRecords(deps.recordDir),
    agent,
    duty: { sessionId: (await readDutySession(deps.incidentDir)) ?? delivery?.sessionId ?? null, lastDelivery: delivery },
  }
}
