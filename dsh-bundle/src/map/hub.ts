/**
 * 地图指令中心：把“地图上要发生什么”（定位、标记、清除、视角）广播给所有已连接的网页端，
 * 并保存当前状态快照，新连接（或刷新页面）先收到快照，标记不会丢。
 *
 * 只做转发和状态保存，不做领域判断：标记的内容取自记录、由调用方（工具或固定流程）校验后传入。
 */

export type MapOp = 'focus' | 'mark' | 'unmark' | 'clear' | 'view' | 'reset' | 'figure' | 'unfigure'

export interface MapCommand {
  op: MapOp
  /** 地图上的名称（几何数据里 names 索引的键），focus、mark 用 */
  name?: string
  /** 标记编号：同一个编号再次 mark 是更新，unmark 按它删除 */
  id?: string
  /** 放大倍数（focus） */
  zoom?: number
  /** 标记的等级或颜色语义：info / warn / alarm / ok，或预警等级 blue / yellow / orange / red */
  level?: string
  /** 标记旁的短文字 */
  text?: string
  /** 视角：倾斜角度（度，view） */
  tilt?: number
  /** 图元（figure 用；unfigure 只要 id） */
  fig?: MapFigure
}

/**
 * 图元：撤人路线、污染巷道、受威胁人员（docs/design-v4-evacuation.md）。只由代码（autopilot）从记录生成，
 * 智能体的 map_view 没有这个能力；坐标是地图坐标（几何数据的坐标系）。
 */
export interface MapFigure {
  id: string
  /** line：折线（污染巷道、撤人路线）；group：一组受威胁人员（人数）；target：撤离终点（出口、避难硐室） */
  type: 'line' | 'group' | 'target'
  /** line：contaminated | route；group：threatened | blocked；target：exit | refuge */
  kind: string
  points?: [number, number][]
  at?: [number, number]
  /** 旁边的短文字（照抄记录里的值，如“约 1.6 分钟”“9 人”） */
  text?: string
  /** 数值：污染巷道的到达时间（分钟，用于着色）、一组的人数；记录原值 */
  value?: number | null
  /** 记录已过期：画成灰色 */
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
  figures: MapFigure[]
}

const OPS = new Set<MapOp>(['focus', 'mark', 'unmark', 'clear', 'view', 'reset', 'figure', 'unfigure'])
const LEVELS = new Set(['info', 'warn', 'alarm', 'ok', 'blue', 'yellow', 'orange', 'red'])
const MAX_MARKS = 60
const MAX_FIGURES = 120
const MAX_POINTS = 60
const FIGURE_TYPES = new Set(['line', 'group', 'target'])

export class MapHub {
  private seq = 0
  private marks = new Map<string, MapMark>()
  private figures = new Map<string, MapFigure>()
  private focus: MapSnapshot['focus'] = null
  private tilt: number | null = null
  private readonly clients = new Set<(chunk: string) => void>()

  /** 校验并执行一条指令，成功返回广播的事件，输入不合格抛 Error（说明可直接给调用方）。 */
  emit(cmd: MapCommand): { seq: number; cmd: MapCommand } {
    if (!OPS.has(cmd.op)) throw new Error(`未知操作：${String(cmd.op)}`)
    if ((cmd.op === 'focus' || cmd.op === 'mark') && !cmd.name) throw new Error(`${cmd.op} 需要 name`)
    if (cmd.level !== undefined && !LEVELS.has(cmd.level)) throw new Error(`level 只能是 info/warn/alarm/ok/blue/yellow/orange/red：${cmd.level}`)
    if (cmd.zoom !== undefined && !(cmd.zoom >= 0.5 && cmd.zoom <= 20)) throw new Error('zoom 要在 0.5 到 20 之间')
    if (cmd.tilt !== undefined && !(cmd.tilt >= 0 && cmd.tilt <= 60)) throw new Error('tilt 要在 0 到 60 度之间')
    if (cmd.op === 'figure') {
      cmd = { ...cmd, fig: this.checkFigure(cmd.fig) }
      this.figures.set(cmd.fig!.id, cmd.fig!)
    } else if (cmd.op === 'unfigure') {
      if (!cmd.fig?.id || !this.figures.delete(cmd.fig.id)) throw new Error(`没有这个图元：${cmd.fig?.id ?? ''}`)
    } else if (cmd.op === 'mark') {
      const id = cmd.id ?? `m-${this.seq + 1}`
      if (!this.marks.has(id) && this.marks.size >= MAX_MARKS) throw new Error(`标记最多 ${MAX_MARKS} 个，先清除一些`)
      this.marks.set(id, { id, name: cmd.name!, level: cmd.level ?? 'info', text: (cmd.text ?? '').slice(0, 60) })
      cmd = { ...cmd, id }
    } else if (cmd.op === 'unmark') {
      if (!cmd.id || !this.marks.delete(cmd.id)) throw new Error(`没有这个标记：${cmd.id ?? ''}`)
    } else if (cmd.op === 'clear') {
      this.marks.clear()
      this.focus = null
    } else if (cmd.op === 'focus') {
      this.focus = { name: cmd.name!, ...(cmd.zoom === undefined ? {} : { zoom: cmd.zoom }) }
    } else if (cmd.op === 'view') {
      this.tilt = cmd.tilt ?? this.tilt
    } else if (cmd.op === 'reset') {
      this.focus = null
      this.tilt = 0
    }
    const event = { seq: ++this.seq, cmd }
    this.write(`event: cmd\ndata: ${JSON.stringify(event)}\n\n`)
    return event
  }

  snapshot(): MapSnapshot {
    return { seq: this.seq, marks: [...this.marks.values()], focus: this.focus, tilt: this.tilt, figures: [...this.figures.values()] }
  }

  private checkFigure(f: MapFigure | undefined): MapFigure {
    if (!f || typeof f.id !== 'string' || !f.id) throw new Error('图元需要 id')
    if (!FIGURE_TYPES.has(f.type)) throw new Error(`图元类型只能是 line/group/target：${String(f.type)}`)
    if (!this.figures.has(f.id) && this.figures.size >= MAX_FIGURES) throw new Error(`图元最多 ${MAX_FIGURES} 个`)
    const num = (v: unknown): boolean => typeof v === 'number' && Number.isFinite(v)
    const pt = (p: unknown): boolean => Array.isArray(p) && p.length === 2 && num(p[0]) && num(p[1])
    if (f.type === 'line') {
      if (!Array.isArray(f.points) || f.points.length < 2 || f.points.length > MAX_POINTS || !f.points.every(pt)) throw new Error(`折线要 2 到 ${MAX_POINTS} 个有效的点`)
    } else if (!pt(f.at)) throw new Error(`${f.type} 图元需要 at：[x, y]`)
    if (f.value !== undefined && f.value !== null && !num(f.value)) throw new Error('value 要是数字')
    return { ...f, ...(f.text === undefined ? {} : { text: f.text.slice(0, 60) }) }
  }

  /** 登记一个连接，返回取消登记的函数；登记时先发一次快照。 */
  subscribe(write: (chunk: string) => void): () => void {
    this.clients.add(write)
    write(`event: snapshot\ndata: ${JSON.stringify(this.snapshot())}\n\n`)
    return () => this.clients.delete(write)
  }

  get clientCount(): number {
    return this.clients.size
  }

  private write(chunk: string): void {
    for (const w of this.clients) {
      try {
        w(chunk)
      } catch {
        this.clients.delete(w)
      }
    }
  }
}

/**
 * 进程内共享的指令中心。DSH 加载插件时模块可能被重复实例化（热更新等），所以挂在 globalThis 上，
 * 地图插件（浏览器通道）和智能体工具（map_view）拿到的是同一个。
 */
const HUB_KEY = Symbol.for('rescue.map.hub')
export function getHub(): MapHub {
  const g = globalThis as unknown as Record<symbol, MapHub | undefined>
  return (g[HUB_KEY] ??= new MapHub())
}

