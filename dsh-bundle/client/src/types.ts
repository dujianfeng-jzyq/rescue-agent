export type LayerMap = Record<string, { aci: number; lines: { p: number[]; c?: number; aci?: number }[] }>
/** 实心填充，按 ACI 颜色分组；o 是外轮廓，h 是孔洞（坐标都是 x,y 交替的一维数组） */
export type FillMap = Record<string, { o: number[]; h?: number[][] }[]>

export interface Legend {
  /** 图例框大小；内部坐标相对框的左下角 */
  size: [number, number]
  layers: LayerMap
  fills: FillMap
  labels: { text: string; x: number; y: number; h: number; m?: number }[]
}

/** 底图几何（scripts/map/extract_geometry.py 的输出，地图包里的 geometry.json） */
export interface Geometry {
  version: number
  view: string
  title: string
  bbox: [number, number, number, number]
  layers: LayerMap
  fills: FillMap
  legend: Legend | null
  labels: { id: string; text: string; x: number; y: number; h: number; rot: number; kind: 'name' | 'flow' | 'section' | 'note' }[]
  names: Record<string, string[]>
}

export type MapOp = 'focus' | 'mark' | 'unmark' | 'clear' | 'view' | 'reset' | 'figure' | 'unfigure'

/** 与 Node 侧 hub.ts 一致 */
export interface MapCommand {
  op: MapOp
  name?: string
  id?: string
  zoom?: number
  level?: string
  text?: string
  tilt?: number
  fig?: MapFigure
}

/** 与 Node 侧 hub.ts 一致：图元只由代码从记录生成 */
export interface MapFigure {
  id: string
  type: 'line' | 'group' | 'target'
  kind: string
  points?: [number, number][]
  at?: [number, number]
  value?: number | null
  text?: string
  stale?: boolean
}

export interface MapMark {
  id: string
  name: string
  level: string
  text: string
}

export interface MapSnapshot {
  seq: number
  marks: MapMark[]
  focus: { name: string; zoom?: number } | null
  tilt: number | null
  figures?: MapFigure[]
}
