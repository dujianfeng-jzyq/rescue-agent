/**
 * 地图映射表（地图包里的 mine_map.yaml）：矿井的工作面、巷道、测点 ↔ 地图上的名称。
 *
 * 地图上的位置只认几何数据 names 索引里的名称（智能体和界面都不报坐标）。同一个矿井换一张图、
 * 或同一张图换一个演示矿井，都只改这张表。映射表跟着地图包走（真实矿井图不进仓库，映射表也不进）。
 *
 * 格式（例子见 scripts/map/mine_map.example.yaml）：
 *   status / reviewed_by: 映射的核对状态；placeholder 或没有 reviewed_by 时界面标“示意映射”
 *   panels:   { <工作面编号>: { anchor: <地图名称>, goaf_anchor: <地图名称，可选，火区和采空区测点用> } }
 *   roadways: { <巷道编号>: <地图名称> | { name: <地图名称，可选>, via: [[x, y], ...] 可选的途经点 } }
 *   sensors:  { <测点编号>: <地图名称> }   # 覆盖：个别测点不在它所在巷道的位置时才写
 *   nodes:    { <网络节点编号>: { at: [x, y] } }   # 撤人路线用：节点在图上的位置（地图坐标）；巷道折线 = 起点节点 → via → 终点节点
 *
 * 校验：名称不在几何数据里的条目丢弃并记警告（fail closed：宁可不显示，也不把标记画到错的地方）。
 */

import { existsSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { parse } from 'yaml'

export interface PanelAnchor {
  anchor: string
  goafAnchor?: string
}

export type Pt = [number, number]

export interface MineMap {
  panels: Record<string, PanelAnchor>
  roadways: Record<string, string>
  sensors: Record<string, string>
  /** 网络节点在图上的位置（地图坐标） */
  nodes: Record<string, Pt>
  /** 巷道折线的途经点（起点节点和终点节点之间） */
  via: Record<string, Pt[]>
  /** 映射的核对状态；不是 reviewed 就是“示意映射” */
  status: string | null
  reviewedBy: string | null
}

export const EMPTY_MAP: MineMap = { panels: {}, roadways: {}, sensors: {}, nodes: {}, via: {}, status: null, reviewedBy: null }

/** 没有核对人（或状态还是 placeholder）的映射是“示意映射”，界面上要标明。 */
export function isSchematic(m: MineMap): boolean {
  return !m.reviewedBy || m.status === 'placeholder'
}

export interface SensorRef {
  id: string
  roadway_id?: string | null
  panel?: string | null
  /** 采空区测点（用采空区锚点） */
  role?: string | null
}

export function parseMineMap(text: string, known: ReadonlySet<string> | null, bbox: [number, number, number, number] | null = null): { map: MineMap; warnings: string[] } {
  const warnings: string[] = []
  const raw = (parse(text) ?? {}) as Record<string, any>
  const ok = (name: unknown, where: string): name is string => {
    if (typeof name !== 'string' || !name) {
      warnings.push(`${where}：名称不是字符串`)
      return false
    }
    if (known && !known.has(name)) {
      warnings.push(`${where}：地图上没有叫“${name}”的位置，已忽略`)
      return false
    }
    return true
  }
  const point = (v: unknown, where: string): Pt | null => {
    if (!Array.isArray(v) || v.length !== 2 || !v.every((n) => typeof n === 'number' && Number.isFinite(n))) {
      warnings.push(`${where}：位置要写成 [x, y]（两个数）`)
      return null
    }
    if (bbox) {
      const mx = (bbox[2] - bbox[0]) * 0.05
      const my = (bbox[3] - bbox[1]) * 0.05
      if (v[0]! < bbox[0] - mx || v[0]! > bbox[2] + mx || v[1]! < bbox[1] - my || v[1]! > bbox[3] + my) {
        warnings.push(`${where}：位置 [${v[0]}, ${v[1]}] 在图的范围之外，已忽略`)
        return null
      }
    }
    return [v[0]!, v[1]!]
  }
  const map: MineMap = { panels: {}, roadways: {}, sensors: {}, nodes: {}, via: {}, status: typeof raw.status === 'string' ? raw.status : null, reviewedBy: typeof raw.reviewed_by === 'string' ? raw.reviewed_by : null }
  for (const [id, v] of Object.entries((raw.nodes ?? {}) as Record<string, any>)) {
    const p = point(v?.at, `nodes.${id}.at`)
    if (p) map.nodes[id] = p
  }
  for (const [id, v] of Object.entries((raw.panels ?? {}) as Record<string, any>)) {
    if (!ok(v?.anchor, `panels.${id}.anchor`)) continue
    const entry: PanelAnchor = { anchor: v.anchor }
    if (v.goaf_anchor !== undefined && ok(v.goaf_anchor, `panels.${id}.goaf_anchor`)) entry.goafAnchor = v.goaf_anchor
    map.panels[id] = entry
  }
  for (const [id, v] of Object.entries((raw.roadways ?? {}) as Record<string, unknown>)) {
    if (typeof v === 'string' || v === undefined || v === null) {
      if (typeof v === 'string' && ok(v, `roadways.${id}`)) map.roadways[id] = v
      continue
    }
    const o = v as { name?: unknown; via?: unknown }
    if (o.name !== undefined && ok(o.name, `roadways.${id}.name`)) map.roadways[id] = o.name
    if (o.via !== undefined) {
      if (!Array.isArray(o.via)) warnings.push(`roadways.${id}.via：要写成 [[x, y], ...]`)
      else {
        const pts = o.via.map((p, i) => point(p, `roadways.${id}.via[${i}]`))
        if (pts.every((p) => p !== null)) map.via[id] = pts as Pt[]
        else warnings.push(`roadways.${id}.via：有无效的点，整条途经点已忽略（路线会按直线画）`)
      }
    }
  }
  for (const [id, name] of Object.entries((raw.sensors ?? {}) as Record<string, unknown>)) if (ok(name, `sensors.${id}`)) map.sensors[id] = name
  return { map, warnings }
}

/** 测点在地图上的位置：先看测点自己的覆盖，再看所在巷道，最后是工作面的锚点（采空区测点用采空区锚点）。 */
export function anchorForSensor(map: MineMap, s: SensorRef): string | null {
  const direct = map.sensors[s.id]
  if (direct) return direct
  const byRoadway = s.roadway_id ? map.roadways[s.roadway_id] : undefined
  if (byRoadway) return byRoadway
  const p = s.panel ? map.panels[s.panel] : undefined
  if (!p) return null
  return s.role === 'goaf_gas_tube' || s.role === 'goaf_temperature' ? (p.goafAnchor ?? p.anchor) : p.anchor
}

export function anchorForPanel(map: MineMap, panel: string | null | undefined, goaf = false): string | null {
  const p = panel ? map.panels[panel] : undefined
  if (!p) return null
  return goaf ? (p.goafAnchor ?? p.anchor) : p.anchor
}

/** 地图包里的映射表加载器：按文件修改时间缓存，改了文件不用重启。 */
export class MineMapLoader {
  private cached: { mtime: number; names: number; map: MineMap; warnings: string[] } | null = null

  private readonly dir: string
  private readonly geometry: () => { names: ReadonlySet<string>; bbox: [number, number, number, number] } | null

  constructor(dir: string, geometry: () => { names: ReadonlySet<string>; bbox: [number, number, number, number] } | null) {
    this.dir = dir
    this.geometry = geometry
  }

  get(): { map: MineMap; warnings: string[] } {
    const file = join(this.dir, 'mine_map.yaml')
    if (!existsSync(file)) return { map: EMPTY_MAP, warnings: [] }
    const geo = this.geometry()
    const known = geo?.names ?? null
    const mtime = statSync(file).mtimeMs
    const names = known?.size ?? -1
    if (this.cached && this.cached.mtime === mtime && this.cached.names === names) return this.cached
    try {
      this.cached = { mtime, names, ...parseMineMap(readFileSync(file, 'utf8'), known, geo?.bbox ?? null) }
    } catch (e) {
      this.cached = { mtime, names, map: EMPTY_MAP, warnings: [`mine_map.yaml 解析失败：${(e as Error).message}`] }
    }
    return this.cached
  }
}
