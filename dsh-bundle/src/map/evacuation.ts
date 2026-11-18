/**
 * 撤人路线与受威胁人员（docs/design-v4-evacuation.md）：把 fire-assessment/personnel 技能的结果记录画到地图上。
 *
 * 只显示，不计算：污染巷道、到达时间、各组人数、路线、长度、步行时间都是记录里的原值，这里不改、不取整、不补。
 * 这里只做两件事：选哪份记录（以及它有多旧），和用映射表把“巷道/节点编号”换成图上的位置。
 *
 * 宁可不画，也不画错：
 *   - 记录没有计算（computed=false）或过期太久：不画，界面上写原因；
 *   - 路线里有一段巷道在图上没有映射（节点没有位置）：整条路线不画，只在列表里写“有 N 段没有映射”；
 *   - 路线走不通：不画线，写记录里的原话“需人工研判”。
 * 图上的折线只表示走哪几段，不表示距离：长度、时间一律用记录里的值。
 *
 * 外部为主、我方兜底（docs/design-v6-route-service.md、design-v7-fire-sim.md 第 3.1 节）：
 *   - 避灾路线：同一次研判的 route_compare 记录（输入的 personnel 就是这份记录）里每组的 display 就是要显示的路线
 *     （选择在技能脚本里做）：外部服务的路线画成 route，我方路线画成 route_local（标签都写“路径规划”，只用颜色区分来源）；
 *     没有 route_compare 记录（没配置外部服务、那一段失败）时画我方路线（route_local）。每组只画一条。
 *   - 蔓延范围：同一次研判的 fire_sim_compare 记录可用（display_source = service）时只画推演中 CO 达到“到达”浓度的巷道
 *     （fire_spread，带到达和危险时刻，前端据此按时间回放），不画我方污染巷道；否则画我方污染巷道（contaminated）。两种标签都写“灾害推演”，只用颜色区分来源。
 *   撤人通知按同一份 route_compare 取路线（validation.evacuation_routes），图上和通知一致。
 */

import { readdir, readFile, stat } from 'node:fs/promises'
import { join } from 'node:path'

import type { MapFigure } from './hub.ts'
import { isSchematic } from './mapping.ts'
import type { MineMap, Pt } from './mapping.ts'
import type { RecordLike } from './record-label.ts'

/** 记录的时效（占位值，待专家审定）：不晚于它是新鲜的，之后变灰，再久就不显示。单位：矿井小时。 */
export const FRESH_H = 2
export const HIDE_H = 6

export interface NetworkNode {
  id: string
  name?: string
  is_surface?: boolean
  is_refuge?: boolean
}

export interface NetworkRoadway {
  id: string
  name: string
  from_node: string
  to_node: string
}

export interface Network {
  nodes: NetworkNode[]
  roadways: NetworkRoadway[]
}

export type Source = 'service' | 'ours'

export interface RouteView {
  /** service：外部路径规划服务的路线；ours：本系统计算（外部服务不可用、没有结果） */
  source: Source
  sourceText: string
  /** 用本系统路线的原因；用外部服务的路线时为 null */
  reason: string | null
  reachable: boolean
  targetName: string | null
  lengthM: number | null
  walkMin: number | null
  selfRescuerMin: number | null
  /** 外部服务的暂时脱险时间（分钟）和烟中通行时间（秒）；本系统路线为 null */
  escapeMin: number | null
  smokeTimeS: number | null
  /** 记录里的原话（走不通时，如“避开受污染巷道后无可达出口或避难硐室，需人工研判”） */
  note: string | null
  /** 图上画出来了吗 */
  drawn: boolean
  /** 没画出来的原因：路线里缺映射的巷道/节点 */
  missing: string[]
}

export interface GroupView {
  roadwayId: string
  roadwayName: string
  /** 地图上的名称（点击定位用）；没有映射为 null */
  anchor: string | null
  count: number
  /** 有害气体到达这一组所在巷道的时刻（分钟）：推演可用时取推演（CO 达到“到达”浓度，推演时长内没到为 null），否则取本系统估算 */
  arrival: { source: Source; min: number | null }
  route: RouteView
}

/** 外部火灾推演服务的结果（试验性）：数值全部照抄 fire_sim_compare 记录 */
export interface FireSpreadView {
  recordId: string
  /** ok：有推演结果；unavailable：推演不可用（note 写原因），蔓延范围按本系统估算显示 */
  status: 'ok' | 'unavailable'
  note: string | null
  /** 推演时长（分钟） */
  horizonMin: number | null
  /** 判读用的 CO 浓度（ppm，记录的参数）：到达、危险 */
  reachPpm: number | null
  dangerPpm: number | null
  /** 推演中 CO 达到“到达”浓度的巷道（按到达先后），漏风通道也在内 */
  roadways: { roadwayId: string; name: string; reachMin: number; dangerMin: number | null; peakPpm: number; reversed: boolean; drawn: boolean }[]
  /** 风流反转或停风的巷道 */
  flowChanged: string[]
  /** 记录里显示用的整句（display_lines，报告里的那几行） */
  lines: string[]
}

export interface EvacuationView {
  recordId: string
  asOf: string
  panel: string
  computed: boolean
  /** 没有计算时记录里的原因 */
  note: string | null
  method: string | null
  totalThreatened: number | null
  totalUnderground: number | null
  /** 记录比当前矿井时间旧多少小时；没有矿井时间为 null */
  ageHours: number | null
  freshness: 'fresh' | 'grey' | 'hidden' | 'unknown'
  /** 映射表是“示意映射”（没核对） */
  schematic: boolean
  contaminated: { roadwayId: string; name: string; arrivalMin: number; drawn: boolean }[]
  groups: GroupView[]
  /** 要画的图元；记录不显示（未计算、已过期）时为空 */
  figures: MapFigure[]
  /** 按已下发的撤人通知显示时，通知的指令编号（路线就是通知里的那份记录） */
  noticeActionId?: string | null
  /** 选定避灾路线的 route_compare 记录编号；没有为 null（全部按本系统路线） */
  routeRecordId: string | null
  /** 蔓延范围显示哪一种：service 推演（fireSpread 可用）；ours 本系统估算 */
  spreadSource: Source
  /** 外部火灾推演的结果；没有 fire_sim_compare 记录为 null */
  fireSpread: FireSpreadView | null
}

export const SOURCE_TEXT: Record<Source, string> = { service: '外部路径规划服务', ours: '本系统计算' }
const NO_ROUTE_RECORD = '没有外部路径规划服务的结果（未配置或这次研判未返回）'

type Json = Record<string, any>

const mineMs = (t: string): number => Date.parse(`${t.slice(0, 19)}Z`)

export function ageHours(asOf: string, mineNow: string | null): number | null {
  if (!mineNow) return null
  const a = mineMs(asOf)
  const b = mineMs(mineNow)
  return Number.isFinite(a) && Number.isFinite(b) ? Math.max(0, (b - a) / 3_600_000) : null
}

export function freshnessOf(age: number | null): EvacuationView['freshness'] {
  if (age === null) return 'unknown'
  return age <= FRESH_H ? 'fresh' : age <= HIDE_H ? 'grey' : 'hidden'
}

/** 折线上按长度比例 frac（0 起点，1 终点）取的点，不是顶点的平均（折线弯的时候平均点会落到线外） */
export function pointAlong(pts: Pt[], frac: number): Pt {
  const seg = pts.slice(1).map((p, i) => Math.hypot(p[0] - pts[i]![0], p[1] - pts[i]![1]))
  let rest = seg.reduce((a, b) => a + b, 0) * Math.min(Math.max(frac, 0), 1)
  for (let i = 0; i < seg.length; i++) {
    if (rest <= seg[i]! || i === seg.length - 1) {
      const t = seg[i]! === 0 ? 0 : Math.min(rest / seg[i]!, 1)
      return [pts[i]![0] + (pts[i + 1]![0] - pts[i]![0]) * t, pts[i]![1] + (pts[i + 1]![1] - pts[i]![1]) * t]
    }
    rest -= seg[i]!
  }
  return pts[0]!
}

const midpoint = (pts: Pt[]): Pt => pointAlong(pts, 0.5)

/**
 * 污染巷道的时间标签放在入口一端往里一点：到达时间是烟气到这条巷道入口（风流上游端）的时间，
 * 放在中点或末端会让人以为整条巷道或下游端也是这个时间。不放正好在节点上，免得和上一条巷道的线、标签叠在一起。
 */
export const CONTAMINATION_LABEL_FRAC = 0.15

/** 一条巷道在图上的折线（按 from→to 的方向）：起点节点 → 途经点 → 终点节点；缺节点位置返回缺的节点编号。 */
function roadwayPath(r: NetworkRoadway, map: MineMap): { pts: Pt[] } | { missing: string[] } {
  const a = map.nodes[r.from_node]
  const b = map.nodes[r.to_node]
  const missing = [...(a ? [] : [r.from_node]), ...(b ? [] : [r.to_node])]
  if (missing.length) return { missing }
  return { pts: [a!, ...(map.via[r.id] ?? []), b!] }
}

/**
 * 沿路线把各段巷道的折线接起来：nodes 是从人员所在巷道一端开始的节点序列，roadways 是依次经过的巷道（必要时反向）。
 * start 是人员所在巷道中点（没有映射时为 null，记为缺失）。
 */
function chain(start: Pt | null, startMissing: string[], nodesSeq: string[], roadwayIds: string[], roads: Map<string, NetworkRoadway>, map: MineMap): { pts: Pt[]; missing: string[] } {
  const pts: Pt[] = start ? [start] : []
  const missing = new Set<string>(start ? [] : startMissing)
  roadwayIds.forEach((id, i) => {
    const r = roads.get(id)
    if (!r) {
      missing.add(id)
      return
    }
    const p = roadwayPath(r, map)
    if ('missing' in p) {
      p.missing.forEach((m) => missing.add(m))
      return
    }
    const forward = r.from_node === nodesSeq[i]
    for (const q of forward ? p.pts : [...p.pts].reverse()) pts.push(q)
  })
  return { pts, missing: [...missing] }
}

export function evacuationView(
  record: RecordLike & { as_of?: string },
  network: Network | null,
  map: MineMap,
  mineNow: string | null,
  compare: (RecordLike & { as_of?: string }) | null = null,
  fireCompare: (RecordLike & { as_of?: string }) | null = null,
): EvacuationView {
  const res: Json = record.content?.result ?? {}
  const asOf = String(record.as_of ?? res.as_of ?? '')
  const age = ageHours(asOf, mineNow)
  const freshness = freshnessOf(age)
  const base = {
    recordId: record.record_id,
    asOf,
    panel: String(res.panel ?? record.panel ?? ''),
    note: typeof res.note === 'string' ? res.note : null,
    method: typeof res.method === 'string' ? res.method : null,
    ageHours: age,
    freshness,
    schematic: isSchematic(map),
    routeRecordId: null as string | null,
    spreadSource: 'ours' as Source,
    fireSpread: null as FireSpreadView | null,
  }
  if (!res.computed) {
    return { ...base, computed: false, totalThreatened: null, totalUnderground: null, contaminated: [], groups: [], figures: [] }
  }

  const roads = new Map((network?.roadways ?? []).map((r) => [r.id, r]))
  const nodes = new Map((network?.nodes ?? []).map((n) => [n.id, n]))
  const nameOf = (rid: string): string => roads.get(rid)?.name ?? rid
  const stale = freshness !== 'fresh'
  const show = freshness !== 'hidden'
  const figures: MapFigure[] = []
  // 只认同一次研判的选择记录（输入的 personnel 就是这份记录）
  const cmp: Json | null = compare && (compare.source as Json | undefined)?.inputs?.personnel === record.record_id ? (compare.content?.result ?? null) : null
  const shown = new Map(((cmp?.computed ? cmp.groups : []) as Json[]).map((g) => [String(g.roadway_id), (g.display ?? null) as Json | null]))
  const fireSpread = fireCompare && (fireCompare.source as Json | undefined)?.inputs?.personnel === record.record_id ? fireSpreadView(fireCompare, roads, map, show, stale, figures) : null
  const spreadSource: Source = fireSpread?.status === 'ok' ? 'service' : 'ours'
  const simGroups = new Map(((fireSpread?.status === 'ok' ? (fireCompare!.content as Json)?.result?.groups : []) ?? []).map((g: Json) => [String(g.roadway_id), g]))

  const contaminated = ((res.contaminated_roadways ?? []) as Json[]).map((c) => {
    const r = roads.get(c.roadway_id)
    const path = r ? roadwayPath(r, map) : { missing: [String(c.roadway_id)] }
    const drawn = 'pts' in path && spreadSource === 'ours'
    if (drawn && show) figures.push({ id: `auto:evac:cont:${c.roadway_id}`, type: 'line', kind: 'contaminated', points: path.pts, at: pointAlong(path.pts, CONTAMINATION_LABEL_FRAC), value: c.arrival_min, text: `灾害推演 · 约 ${c.arrival_min} 分钟到达`, stale })
    return { roadwayId: String(c.roadway_id), name: nameOf(c.roadway_id), arrivalMin: Number(c.arrival_min), drawn }
  })

  const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
  const groups: GroupView[] = ((res.threatened ?? []) as Json[]).map((g) => {
    const rid = String(g.roadway_id)
    const road = roads.get(rid)
    const own = road ? roadwayPath(road, map) : { missing: [rid] }
    const d: Json = shown.get(rid) ?? { ...(g.escape_route ?? {}), source: 'ours', reason: cmp ? null : NO_ROUTE_RECORD }
    const source: Source = d.source === 'service' ? 'service' : 'ours'
    const route: RouteView = {
      source,
      sourceText: SOURCE_TEXT[source],
      reason: source === 'ours' ? (typeof d.reason === 'string' ? d.reason : NO_ROUTE_RECORD) : null,
      reachable: !!d.reachable,
      targetName: typeof d.target_name === 'string' ? d.target_name : null,
      lengthM: num(d.length_m),
      walkMin: num(d.walk_min),
      selfRescuerMin: num(d.walk_with_self_rescuer_min),
      escapeMin: num(d.escape_min),
      smokeTimeS: num(d.smoke_time_s),
      note: typeof d.note === 'string' ? d.note : null,
      drawn: false,
      missing: [],
    }
    const sg = simGroups.get(rid) as Json | undefined
    const arrival: GroupView['arrival'] = spreadSource === 'service' ? { source: 'service', min: num(sg?.sim_reach_min) } : { source: 'ours', min: num(g.arrival_min) }
    const groupAt = 'pts' in own ? midpoint(own.pts) : null
    if (groupAt && show) {
      figures.push({
        id: `auto:evac:group:${rid}`,
        type: 'group',
        kind: route.reachable ? 'threatened' : 'blocked',
        at: groupAt,
        value: g.count,
        text: route.reachable ? `${g.count} 人` : `${g.count} 人 · 无可行路线，需人工研判`,
        stale,
      })
    }
    const ownMissing = 'missing' in own ? own.missing : []
    if (route.reachable && Array.isArray(d.nodes) && Array.isArray(d.roadways)) {
      const { pts, missing } = chain(groupAt, ownMissing, d.nodes as string[], d.roadways as string[], roads, map)
      route.missing = missing
      if (missing.length === 0 && pts.length >= 2) {
        route.drawn = true
        if (show) {
          // 界面上只写“路径规划”，来源只用颜色区分（route 绿色、route_local 蓝色）
          const label = source === 'service' ? (route.lengthM === null ? '路径规划' : `路径规划 · 约 ${route.lengthM} 米`) : route.walkMin === null ? '路径规划' : `路径规划 · 约 ${route.walkMin} 分钟`
          figures.push({ id: `auto:evac:route:${rid}`, type: 'line', kind: source === 'service' ? 'route' : 'route_local', points: pts, at: midpoint(pts), text: label, stale })
          const tgt = nodes.get(String(d.target))
          const at = map.nodes[String(d.target)]
          if (at) figures.push({ id: `auto:evac:target:${rid}`, type: 'target', kind: tgt?.is_refuge ? 'refuge' : 'exit', at, text: route.targetName ?? undefined, stale })
        }
      }
    }
    return { roadwayId: rid, roadwayName: nameOf(rid), anchor: map.roadways[rid] ?? null, count: Number(g.count), arrival, route }
  })

  return {
    ...base,
    fireSpread,
    spreadSource,
    computed: true,
    totalThreatened: typeof res.total_threatened === 'number' ? res.total_threatened : null,
    totalUnderground: typeof res.total_underground === 'number' ? res.total_underground : null,
    contaminated,
    groups,
    figures,
    routeRecordId: cmp ? compare!.record_id : null,
  }
}

/**
 * 外部火灾推演：fire_sim_compare 记录 → 左栏的文字和图上的一层（每条推演中 CO 到达的巷道一条线）。
 * 线上带到达时刻（value）和危险时刻（danger），都是记录原值；巷道没有映射时不画，列表里照常列出。
 */
function fireSpreadView(rec: RecordLike, roads: Map<string, NetworkRoadway>, map: MineMap, show: boolean, stale: boolean, figures: MapFigure[]): FireSpreadView {
  const content: Json = (rec.content as Json | undefined) ?? {}
  const r: Json = content.result ?? {}
  const params: Json = content.params ?? {}
  const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
  const view: FireSpreadView = {
    recordId: rec.record_id,
    status: r.status === 'ok' ? 'ok' : 'unavailable',
    note: typeof r.note === 'string' ? r.note : null,
    horizonMin: num(r.horizon_min),
    reachPpm: num(params.co_reach_ppm),
    dangerPpm: num(params.co_danger_ppm),
    roadways: [],
    flowChanged: [],
    lines: Array.isArray(r.display_lines) ? r.display_lines.map(String) : [],
  }
  if (view.status !== 'ok') return view
  for (const x of (r.roadways ?? []) as Json[]) {
    const rid = String(x.roadway_id)
    if (x.air_reversed || x.air_stopped) view.flowChanged.push(rid)
    const reach = num(x.reach_min)
    if (reach === null) continue
    const danger = num(x.danger_min)
    const road = roads.get(rid)
    const path = road ? roadwayPath(road, map) : null
    const drawn = !!path && 'pts' in path
    if (drawn && show) {
      const pts = (path as { pts: Pt[] }).pts
      figures.push({ id: `auto:fire:${rid}`, type: 'line', kind: 'fire_spread', points: pts, at: pointAlong(pts, 0.6), value: reach, danger, text: `灾害推演 · ${reach} 分钟到达`, stale })
    }
    view.roadways.push({ roadwayId: rid, name: road?.name ?? String(x.name ?? rid), reachMin: reach, dangerMin: danger, peakPpm: num(x.peak_ppm) ?? 0, reversed: !!(x.air_reversed || x.air_stopped), drawn })
  }
  return view
}

/**
 * 该工作面最近一份 personnel 记录（按记录里的 as_of，不看文件时间；没有计算的也算：等级回落后最新的是“未计算”，
 * 不能退回去显示更早的“已计算”）。记录目录按修改时间只读最近的几十份，目录没变就用缓存。
 */
type PRec = RecordLike & { as_of?: string }
let cache: { dir: string; mtime: number; panel: string; recs: PRec[]; compares: PRec[]; fires: PRec[] } | null = null
const SCAN_LIMIT = 40

/**
 * 按编号读一份 personnel 记录（撤人通知引用的那份）；不是该工作面的 personnel 记录、读不到都返回 null。
 */
export async function personnelRecordById(dir: string, id: string, panel: string): Promise<PRec | null> {
  if (!/^RS-\d{8}T\d{6}-[a-z0-9]{6}$/.test(id)) return null
  try {
    const r = JSON.parse(await readFile(join(dir, `${id}.json`), 'utf8')) as PRec
    if (r.source?.skill !== 'fire-assessment' || r.source?.script !== 'personnel') return null
    if ((r.content?.result as Json | undefined)?.panel !== panel && r.panel !== panel) return null
    return r
  } catch {
    return null
  }
}

/** 读记录目录里最近的几十份结果记录，留下该工作面的 personnel、route_compare、fire_sim_compare 记录；目录没变就用缓存。 */
async function scan(dir: string, panel: string, mtime: number): Promise<NonNullable<typeof cache>> {
  if (cache && cache.dir === dir && cache.mtime === mtime && cache.panel === panel) return cache
  const names = (await readdir(dir).catch(() => [] as string[])).filter((n) => /^RS-\d{8}T\d{6}-[a-z0-9]{6}\.json$/.test(n))
  const withTime = await Promise.all(names.map(async (n) => ({ n, t: await stat(join(dir, n)).then((s) => s.mtimeMs, () => 0) })))
  const recs: PRec[] = []
  const compares: PRec[] = []
  const fires: PRec[] = []
  for (const { n } of withTime.sort((a, b) => b.t - a.t).slice(0, SCAN_LIMIT)) {
    try {
      const r = JSON.parse(await readFile(join(dir, n), 'utf8')) as PRec
      if (r.source?.skill !== 'fire-assessment') continue
      if ((r.content?.result as Json | undefined)?.panel !== panel && r.panel !== panel) continue
      if (r.source?.script === 'personnel') recs.push(r)
      else if (r.source?.script === 'route_compare') compares.push(r)
      else if (r.source?.script === 'fire_sim_compare') fires.push(r)
    } catch {
      /* 坏文件跳过 */
    }
  }
  cache = { dir, mtime, panel, recs, compares, fires }
  return cache
}

/**
 * 某份 personnel 记录对应的避灾路线选择（route_compare 记录，输入的 personnel 就是它）；只在最近的几十份记录里找，找不到返回 null（按本系统路线画）。
 */
export async function routeCompareFor(dir: string, personnelId: string, panel: string): Promise<PRec | null> {
  let mtime = 0
  try {
    mtime = (await stat(dir)).mtimeMs
  } catch {
    return null
  }
  const c = await scan(dir, panel, mtime)
  return c.compares.find((r) => (r.source as Json | undefined)?.inputs?.personnel === personnelId) ?? null
}

/** 某份 personnel 记录对应的外部火灾推演结果（fire_sim_compare 记录，输入的 personnel 就是它）；找不到返回 null（按本系统估算画）。 */
export async function fireSimCompareFor(dir: string, personnelId: string, panel: string): Promise<PRec | null> {
  let mtime = 0
  try {
    mtime = (await stat(dir)).mtimeMs
  } catch {
    return null
  }
  const c = await scan(dir, panel, mtime)
  return c.fires.find((r) => (r.source as Json | undefined)?.inputs?.personnel === personnelId) ?? null
}

/**
 * notAfter（当前矿井时间）：记录的 as_of 晚于它的忽略——模拟矿井重置时钟后，上一轮演示留下的“未来”记录不能当最新的。
 * 真实矿井时钟不倒退，这条不会生效。
 */
export async function latestPersonnelRecord(dir: string, panel: string, notAfter?: string | null): Promise<PRec | null> {
  let mtime = 0
  try {
    mtime = (await stat(dir)).mtimeMs
  } catch {
    return null
  }
  const c = await scan(dir, panel, mtime)
  let best: PRec | null = null
  for (const r of c.recs) {
    const at = String(r.as_of ?? '')
    if (notAfter && at.slice(0, 19) > notAfter.slice(0, 19)) continue
    if (!best || at > String(best.as_of ?? '')) best = r
  }
  return best
}
