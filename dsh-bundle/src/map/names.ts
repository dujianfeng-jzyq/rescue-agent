import { readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

/** 地图包里 geometry.json 的名称索引（names 的键）；按文件修改时间缓存。没有或读不了返回 null。 */
export function geometryNames(dataDir: string): () => ReadonlySet<string> | null {
  let cache: { mtime: number; names: Set<string> } | null = null
  return () => {
    try {
      const file = join(dataDir, 'geometry.json')
      const mtime = statSync(file).mtimeMs
      if (!cache || cache.mtime !== mtime) cache = { mtime, names: new Set(Object.keys((JSON.parse(readFileSync(file, 'utf8')) as { names: Record<string, unknown> }).names)) }
      return cache.names
    } catch {
      return null
    }
  }
}

export interface GeometryInfo {
  names: ReadonlySet<string>
  /** 图的范围 [x0, y0, x1, y1]（地图坐标）；映射表里的节点位置要落在它里面 */
  bbox: [number, number, number, number]
}

/** geometry.json 的名称索引和范围；按文件修改时间缓存。没有或读不了返回 null。 */
export function geometryInfo(dataDir: string): () => GeometryInfo | null {
  let cache: { mtime: number; info: GeometryInfo } | null = null
  return () => {
    try {
      const file = join(dataDir, 'geometry.json')
      const mtime = statSync(file).mtimeMs
      if (!cache || cache.mtime !== mtime) {
        const g = JSON.parse(readFileSync(file, 'utf8')) as { names: Record<string, unknown>; bbox: number[] }
        cache = { mtime, info: { names: new Set(Object.keys(g.names)), bbox: [g.bbox[0]!, g.bbox[1]!, g.bbox[2]!, g.bbox[3]!] } }
      }
      return cache.info
    } catch {
      return null
    }
  }
}
