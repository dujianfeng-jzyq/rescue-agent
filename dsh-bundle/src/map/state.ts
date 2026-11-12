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
import { ageHours, evacuationView, HIDE_H, latestPersonnelRecord, personnelRecordById } from './evacuation.ts'
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
  records: { id: string; kind: string; kindName: string; title: string; panel: string | null; asOf: string; createdAt: string }[]
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
  assessment: '研判', sealing_plan: '封闭方案', firefighting_plan: '防灭火方案', effect_review: '效果复评', unsealing: '启封评估', response_choice: '处置选择',
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
/**
 * 监听器最近一次检测摘要（等级、阶段）。只看监听器写的条目：别的条目也可能带 level 字段
 * （如处置选择 response_choice 的 level 是“这张卡片是哪一级的”），误当成检测会把等级显示成“未研判”（2026-11-11 演示）。
 */
function lastCheck(entries: Entry[]): Record<string, any> | null {
  for (let i = entries.length - 1; i >= 0; i--) {
    const e = entries[i]!
    if (e.by !== 'sentinel') continue
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

/**
 * 最近一次已下发的撤人通知（guard 写进事件的 action 记事）：指令编号、引用的记录、下发时的矿井时间。
 */
export function lastEvacuationNotice(entries: Entry[]): { actionId: string; recordId: string; asOf: string } | null {
  for (let i = entries.length - 1; i >= 0; i--) {
    const d = entries[i]!.data
    if (entries[i]!.kind !== 'note' || d?.type !== 'action' || d.tool !== 'notify_evacuation' || d.status !== 'issued') continue
    const rid = Array.isArray(d.record_ids) ? d.record_ids.find((x: unknown) => typeof x === 'string' && x.startsWith('RS-')) : undefined
    if (typeof rid === 'string') return { actionId: String(d.action_id ?? ''), recordId: rid, asOf: String(d.as_of ?? entries[i]!.at ?? '') }
  }
  return null
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
      out.push({ id: String(r.record_id), kind: String(r.kind), kindName: KIND_NAME[String(r.kind)] ?? String(r.kind), title: recordTitle(r), panel: r.panel ?? null, asOf: String(r.as_of ?? ''), createdAt: String(r.created_at ?? '') })
    } catch {
      /* 坏文件跳过 */
    }
  }
  return out
}

const SKILL_NAME: Record<string, string> = {
  'fire-assessment': '研判', 'effect-review': '效果复评', 'sealing-plan': '封闭方案', 'firefighting-plan': '防灭火方案', 'plan-compliance': '合规审核',
  'explosion-triangle': '爆炸危险性', 'emergency-escalation': '升级建议', 'unsealing-plan': '启封评估', 'fire-incident-management': '事件', 'incident-review': '事件复盘',
}
const PART_NAME: Record<string, string> = {
  warning: '预警等级', stage: '自燃阶段', alarms: '实时报警', trend: '趋势', personnel: '受威胁人员', check: '风险检测', explanation_options: '研判说明', explanation: '研判说明', timeline: '时间线',
  explosion_triangle: '爆炸三角形', escalation: '升级建议', effect: '复评结果', skeleton: '方案骨架', compose: '方案', scope: '审核范围', review: '审核结论',
  unsealing_plan: '启封评估', brief: '要点', report: '报告', plan: '方案', assessment: '研判',
}

const COLLECT_NAME: Record<string, string> = { assessment: '研判取数', check: '检测取数', effect: '复评取数', plan: '方案取数', unseal: '启封取数' }

/** 记录的中文名：技能 + 脚本/模板/采集清单（如“效果复评 · 复评结果”“研判 · 要点”） */
export function recordTitle(r: Record<string, any>): string {
  const s = r.source ?? {}
  const skill = SKILL_NAME[String(s.skill)] ?? String(s.skill ?? '')
  const part = s.script ?? (s.template ? String(s.template).replace(/^templates\//, '').replace(/\.md$/, '') : s.collect_kind ? `${s.collect_kind}` : '')
  if (r.kind === 'evidence') return [skill, `证据（${COLLECT_NAME[String(part)] ?? part}）`].filter(Boolean).join(' · ')
  return [skill, PART_NAME[String(part)] ?? String(part)].filter(Boolean).join(' · ') || String(r.record_id)
}

export interface RecordView {
  id: string
  kind: string
  kindName: string
  title: string
  panel: string | null
  asOf: string
  createdAt: string
  parents: string[]
  /** 报告：Markdown 原文；结果：标量字段；证据：各测点组的测点数和读数条数 */
  /** 报告：Markdown 原文（结果记录显示按它生成的报告时 from 是报告编号）；结果：标量字段；证据：各测点组的测点数和读数条数 */
  body: { type: 'markdown'; text: string; from?: string } | { type: 'fields'; rows: { key: string; value: string }[]; warnings: string[] }
}

const shown = (v: unknown): string | null => {
  if (v === null || v === undefined || v === '') return null
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (typeof v === 'number' || typeof v === 'string') return String(v)
  if (Array.isArray(v) && v.every((x) => ['string', 'number'].includes(typeof x))) return v.length ? v.join('、') : null
  return null
}

/** 一条记录的只读视图（大屏点“最近记录”弹窗用）。编号不合格式或读不到返回 null。证据记录很大，只给汇总。 */
export async function recordView(dir: string, id: string): Promise<RecordView | null> {
  if (!/^(EV|RS|RP)-\d{8}T\d{6}-[a-z0-9]{6}$/.test(id)) return null
  let r: Record<string, any>
  try {
    r = JSON.parse(await readFile(join(dir, `${id}.json`), 'utf8'))
  } catch {
    return null
  }
  const head = {
    id, kind: String(r.kind), kindName: KIND_NAME[String(r.kind)] ?? String(r.kind), title: recordTitle(r), panel: r.panel ?? null,
    asOf: String(r.as_of ?? ''), createdAt: String(r.created_at ?? ''), parents: Array.isArray(r.parent_ids) ? r.parent_ids.map(String) : [],
  }
  const c = r.content
  if (typeof c === 'string') return { ...head, body: { type: 'markdown', text: c } }
  if (r.kind === 'evidence') {
    const rows: { key: string; value: string }[] = []
    for (const [name, g] of Object.entries<any>(c?.groups ?? {})) rows.push({ key: `测点组 ${name}`, value: `${(g.sensors ?? []).length} 个测点，最新值 ${(g.latest ?? []).length} 条，历史读数 ${(g.readings ?? []).length} 条${g.window_h ? `（${g.window_h} 小时）` : ''}` })
    for (const m of c?.missing ?? []) rows.push({ key: `缺 ${m.group}`, value: String(m.reason ?? '') })
    for (const k of ['fire_areas', 'inventory', 'events', 'mine_alarms'] as const) if (Array.isArray(c?.[k])) rows.push({ key: { fire_areas: '火区台账', inventory: '物资台账', events: '安全监控事件', mine_alarms: '矿井报警平台' }[k], value: `${c[k].length} 条` })
    if (c?.personnel) rows.push({ key: '人员定位', value: '有' })
    if (c?.network) rows.push({ key: '通风网络', value: `${(c.network.roadways ?? []).length} 条巷道` })
    if (c?.since) rows.push({ key: '复评起算', value: String(c.since) })
    return { ...head, body: { type: 'fields', rows, warnings: [] } }
  }
  // 结果记录：有按它生成的报告（同一时刻、上级是它）就显示报告原文（中文、照模板），要点优先；没有才列字段
  const stamp = id.slice(3, 18)
  const reports: { text: string; brief: boolean; id: string }[] = []
  for (const n of await readdir(dir).catch(() => [] as string[])) {
    if (!n.startsWith(`RP-${stamp}`)) continue
    try {
      const x = JSON.parse(await readFile(join(dir, n), 'utf8'))
      if (Array.isArray(x.parent_ids) && x.parent_ids.includes(id) && typeof x.content === 'string') reports.push({ text: x.content, brief: /brief/.test(String(x.source?.template)), id: String(x.record_id) })
    } catch {
      /* 坏文件跳过 */
    }
  }
  const rep = reports.find((x) => x.brief) ?? reports[0]
  if (rep) return { ...head, body: { type: 'markdown', text: rep.text, from: rep.id } }
  const res = c?.result ?? {}
  const rows = Object.entries<any>(res).flatMap(([k, v]) => {
    const t = shown(v)
    return t === null ? [] : [{ key: k, value: t }]
  })
  return { ...head, body: { type: 'fields', rows, warnings: Array.isArray(c?.warnings) ? c.warnings.map(String) : [] } }
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
  let notice: ReturnType<typeof lastEvacuationNotice> = null
  if (main) {
    const entries = await store.entries(main.incident_id).catch(() => [] as Entry[])
    delivery = lastDelivery(entries)
    notice = lastEvacuationNotice(entries)
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
      // 撤人通知下发后、在显示时限内，路线按通知引用的那份记录显示：撤人开始后人员定位变了，
      // 之后再算的记录（如编封闭方案时）受威胁人数是 0，按最新的显示会让刚下发的撤人路线消失（2026-11-10 演示复看）
      const age = notice ? ageHours(notice.asOf, mine.now) : null
      const fromNotice = notice && age !== null && age <= HIDE_H && !(mine.now && notice.asOf.slice(0, 19) > mine.now.slice(0, 19))
        ? await personnelRecordById(deps.recordDir, notice.recordId, incident.panel)
        : null
      const rec = fromNotice ?? (await latestPersonnelRecord(deps.recordDir, incident.panel, mine.now))
      if (rec) {
        const computed = !!(rec.content?.result as { computed?: boolean } | undefined)?.computed
        const net = computed && deps.network ? await deps.network().catch(() => null) : null
        evacuation = { ...evacuationView(rec, net, map, mine.now), noticeActionId: fromNotice ? notice!.actionId : null }
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
