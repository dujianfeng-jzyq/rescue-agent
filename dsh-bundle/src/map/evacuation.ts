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
 * 避灾路线第二意见（第六版，docs/design-v6-route-service.md）：同一次研判的 route_compare 记录（输入的 personnel 就是这份记录）
 * 里外部服务给出的路线，另画一条不同样式的“参考路线（外部服务，试验性）”。与我方路线不一致时两条都画，不选。
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

export interface RouteView {
  reachable: boolean
  targetName: string | null
  lengthM: number | null
  walkMin: number | null
  selfRescuerMin: number | null
  /** 记录里的原话（走不通时是“避开受污染巷道后无可达出口或避难硐室，需人工研判”） */
  note: string | null
  /** 图上画出来了吗 */
  drawn: boolean
  /** 没画出来的原因：路线里缺映射的巷道/节点 */
  missing: string[]
}

/** 外部服务的参考路线（第二意见，试验性）：文字照抄 route_compare 记录 */
export interface ReferenceView {
  /** route：给出路线；no_route：没有路线；unavailable：第二意见不可用 */
  status: 'route' | 'no_route' | 'unavailable'
  /** 比较情况的说明（如“两种路线相同”“我方…无路可走；外部服务给出穿过有烟巷道的参考路线，能否穿烟由人判断”） */
  caseText: string
  /** 记录里这一组的整句说明 */
  text: string
  drawn: boolean
  missing: string[]
}

export interface GroupView {
  roadwayId: string
  roadwayName: string
  /** 地图上的名称（点击定位用）；没有映射为 null */
  anchor: string | null
  count: number
  arrivalMin: number
  route: RouteView
  /** 外部服务的参考路线；没有第二意见记录为 null */
  reference: ReferenceView | null
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
  /** 第二意见的 route_compare 记录编号；没有为 null */
  referenceRecordId: string | null
}

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
    referenceRecordId: null as string | null,
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
  // 第二意见只认同一次研判的比较记录（输入的 personnel 就是这份记录）
  const cmp: Json | null = compare && (compare.source as Json | undefined)?.inputs?.personnel === record.record_id ? (compare.content?.result ?? null) : null
  const refGroups = new Map(((cmp?.computed ? cmp.groups : []) as Json[]).map((g) => [String(g.roadway_id), g]))

  const contaminated = ((res.contaminated_roadways ?? []) as Json[]).map((c) => {
    const r = roads.get(c.roadway_id)
    const path = r ? roadwayPath(r, map) : { missing: [String(c.roadway_id)] }
    const drawn = 'pts' in path
    if (drawn && show) figures.push({ id: `auto:evac:cont:${c.roadway_id}`, type: 'line', kind: 'contaminated', points: path.pts, at: pointAlong(path.pts, CONTAMINATION_LABEL_FRAC), value: c.arrival_min, text: `约 ${c.arrival_min} 分钟到达`, stale })
    return { roadwayId: String(c.roadway_id), name: nameOf(c.roadway_id), arrivalMin: Number(c.arrival_min), drawn }
  })

  const groups: GroupView[] = ((res.threatened ?? []) as Json[]).map((g) => {
    const rid = String(g.roadway_id)
    const road = roads.get(rid)
    const own = road ? roadwayPath(road, map) : { missing: [rid] }
    const er: Json = g.escape_route ?? {}
    const route: RouteView = {
      reachable: !!er.reachable,
      targetName: typeof er.target_name === 'string' ? er.target_name : null,
      lengthM: typeof er.length_m === 'number' ? er.length_m : null,
      walkMin: typeof er.walk_min === 'number' ? er.walk_min : null,
      selfRescuerMin: typeof er.walk_with_self_rescuer_min === 'number' ? er.walk_with_self_rescuer_min : null,
      note: typeof er.note === 'string' ? er.note : null,
      drawn: false,
      missing: [],
    }
    const groupAt = 'pts' in own ? midpoint(own.pts) : null
    if (groupAt && show) {
      figures.push({
        id: `auto:evac:group:${rid}`,
        type: 'group',
        kind: route.reachable ? 'threatened' : 'blocked',
        at: groupAt,
        value: g.count,
        text: route.reachable ? `${g.count} 人` : `${g.count} 人 · ${route.note ?? '无可达路线，需人工研判'}`,
        stale,
      })
    }
    const ownMissing = 'missing' in own ? own.missing : []
    if (route.reachable && Array.isArray(er.nodes) && Array.isArray(er.roadways)) {
      const { pts, missing } = chain(groupAt, ownMissing, er.nodes as string[], er.roadways as string[], roads, map)
      route.missing = missing
      if (missing.length === 0 && pts.length >= 2) {
        route.drawn = true
        if (show) {
          figures.push({ id: `auto:evac:route:${rid}`, type: 'line', kind: 'route', points: pts, at: midpoint(pts), text: route.walkMin === null ? undefined : `约 ${route.walkMin} 分钟`, stale })
          const tgt = nodes.get(String(er.target))
          const at = map.nodes[String(er.target)]
          if (at) figures.push({ id: `auto:evac:target:${rid}`, type: 'target', kind: tgt?.is_refuge ? 'refuge' : 'exit', at, text: route.targetName ?? undefined, stale })
        }
      }
    }
    let reference: ReferenceView | null = null
    const rg = refGroups.get(rid)
    if (rg) {
      const svc: Json = rg.service ?? {}
      const status: ReferenceView['status'] = svc.status === 'route' ? 'route' : svc.status === 'no_route' ? 'no_route' : 'unavailable'
      reference = { status, caseText: String(rg.comparison?.case_text ?? ''), text: String(rg.text ?? ''), drawn: false, missing: [] }
      if (status === 'route' && Array.isArray(svc.nodes) && Array.isArray(svc.roadways)) {
        const { pts, missing } = chain(groupAt, ownMissing, svc.nodes as string[], svc.roadways as string[], roads, map)
        reference.missing = missing
        if (missing.length === 0 && pts.length >= 2) {
          reference.drawn = true
          if (show) figures.push({ id: `auto:evac:ref:${rid}`, type: 'line', kind: 'reference', points: pts, at: pointAlong(pts, 0.35), text: '参考路线（外部服务，试验性）', stale })
        }
      }
    }
    return { roadwayId: rid, roadwayName: nameOf(rid), anchor: map.roadways[rid] ?? null, count: Number(g.count), arrivalMin: Number(g.arrival_min), route, reference }
  })

  return {
    ...base,
    computed: true,
    totalThreatened: typeof res.total_threatened === 'number' ? res.total_threatened : null,
    totalUnderground: typeof res.total_underground === 'number' ? res.total_underground : null,
    contaminated,
    groups,
    figures,
    referenceRecordId: cmp ? compare!.record_id : null,
  }
}

/**
 * 该工作面最近一份 personnel 记录（按记录里的 as_of，不看文件时间；没有计算的也算：等级回落后最新的是“未计算”，
 * 不能退回去显示更早的“已计算”）。记录目录按修改时间只读最近的几十份，目录没变就用缓存。
 */
type PRec = RecordLike & { as_of?: string }
let cache: { dir: string; mtime: number; panel: string; recs: PRec[]; compares: PRec[] } | null = null
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

/** 读记录目录里最近的几十份结果记录，留下该工作面的 personnel 和 route_compare 记录；目录没变就用缓存。 */
async function scan(dir: string, panel: string, mtime: number): Promise<NonNullable<typeof cache>> {
  if (cache && cache.dir === dir && cache.mtime === mtime && cache.panel === panel) return cache
  const names = (await readdir(dir).catch(() => [] as string[])).filter((n) => /^RS-\d{8}T\d{6}-[a-z0-9]{6}\.json$/.test(n))
  const withTime = await Promise.all(names.map(async (n) => ({ n, t: await stat(join(dir, n)).then((s) => s.mtimeMs, () => 0) })))
  const recs: PRec[] = []
  const compares: PRec[] = []
  for (const { n } of withTime.sort((a, b) => b.t - a.t).slice(0, SCAN_LIMIT)) {
    try {
      const r = JSON.parse(await readFile(join(dir, n), 'utf8')) as PRec
      if (r.source?.skill !== 'fire-assessment') continue
      if ((r.content?.result as Json | undefined)?.panel !== panel && r.panel !== panel) continue
      if (r.source?.script === 'personnel') recs.push(r)
      else if (r.source?.script === 'route_compare') compares.push(r)
    } catch {
      /* 坏文件跳过 */
    }
  }
  cache = { dir, mtime, panel, recs, compares }
  return cache
}

/**
 * 某份 personnel 记录对应的第二意见（route_compare 记录，输入的 personnel 就是它）；只在最近的几十份记录里找，找不到返回 null（不画参考路线）。
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
