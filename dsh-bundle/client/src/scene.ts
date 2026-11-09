import {
  BufferGeometry,
  Color,
  DoubleSide,
  Float32BufferAttribute,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshBasicMaterial,
  OrthographicCamera,
  Path,
  Plane,
  Raycaster,
  RingGeometry,
  Scene,
  Shape,
  ShapeGeometry,
  Vector2,
  Vector3,
  WebGLRenderer,
} from 'three'

import { Line2 } from 'three/examples/jsm/lines/Line2.js'
import { LineGeometry } from 'three/examples/jsm/lines/LineGeometry.js'
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js'

import { ACI_TINT, fillStyle, THEMES } from './palette.ts'
import type { Geometry, MapCommand, MapFigure, MapMark, MapSnapshot } from './types.ts'

const LEVEL_COLOR: Record<string, string> = {
  info: '#4aa3ff',
  warn: '#ffb020',
  alarm: '#ff4d4f',
  ok: '#35c46a',
  blue: '#4aa3ff',
  yellow: '#f5c518',
  orange: '#ff8a1f',
  red: '#ff4d4f',
}
const LAYER_ROLE: Record<string, 'roadway' | 'note' | 'facility' | 'flow'> = {
  '0': 'roadway',
  注释: 'note',
  设施: 'facility',
  风量: 'flow',
}
const PIN_PX = 40 // 标记竖线的屏幕高度（像素），倾斜时才看得出
const RING_PX = 16 // 标记圆环的屏幕半径（像素）：放大缩小时保持不变
const LABEL_FONT: Record<string, number> = { name: 11, flow: 10, section: 10, note: 10 }
const LABEL_PRIORITY: Record<string, number> = { name: 0, flow: 1, section: 2, note: 3 }

/** 被悬浮面板盖住的边（像素）；定位时把目标放到剩下的空白区域中心。 */
export interface Insets {
  left: number
  right: number
  top: number
  bottom: number
}

interface Mark {
  data: MapMark
  ring: Mesh
  pin: LineSegments
  tag: HTMLDivElement
  x: number
  y: number
  born: number
}

interface Fig {
  data: MapFigure
  line?: Line2
  el?: HTMLDivElement
  x: number
  y: number
}

const FIG_GREY = '#8a8f98'
/** 污染巷道的颜色：到达越早越红（按本批污染巷道的相对到达时间，只是显示，数值以文字为准） */
function contaminationColor(rel: number): string {
  const r = Math.min(Math.max(rel, 0), 1)
  const from = [255, 77, 79]
  const to = [255, 176, 32]
  return `rgb(${from.map((a, i) => Math.round(a + (to[i]! - a) * r)).join(',')})`
}

interface LabelItem {
  el: HTMLDivElement
  x: number
  y: number
  kind: string
  w: number
  h: number
}

interface Tween {
  t0: number
  dur: number
  from: { x: number; y: number; zoom: number }
  to: { x: number; y: number; zoom: number }
}

const ease = (t: number): number => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2)

/** 文字宽度的估计：全角字符 1em，其余约 0.58em。只用来防重叠，不求精确。 */
function textWidth(s: string, fs: number): number {
  let w = 0
  for (const ch of s) w += (ch.codePointAt(0)! > 255 ? 1 : 0.58) * fs
  return w
}

function rectsOverlap(a: [number, number, number, number], b: [number, number, number, number]): boolean {
  return a[0] < b[0] + b[2] && b[0] < a[0] + a[2] && a[1] < b[1] + b[3] && b[1] < a[1] + a[3]
}

/**
 * 通风图地图：正交相机看 XY 平面，平移/缩放/倾斜（绕 X 轴）。
 * 倾斜用来在斜轴测底图上拉开层次，标记的竖线和圆环在倾斜时才立起来。
 */
export class MapView {
  private readonly renderer: WebGLRenderer
  private readonly scene = new Scene()
  private readonly camera = new OrthographicCamera(-1, 1, 1, -1, -5000, 5000)
  private readonly labelLayer = document.createElement('div')
  private readonly geo: Geometry
  private readonly labels: LabelItem[] = []
  private readonly marks = new Map<string, Mark>()
  private readonly figs = new Map<string, Fig>()
  private readonly lineMats: { mat: LineBasicMaterial; role: 'roadway' | 'note' | 'facility' | 'flow'; aci: number | null }[] = []
  private readonly fillMats: { mat: MeshBasicMaterial; aci: string }[] = []
  private readonly ray = new Raycaster()
  private readonly ground = new Plane(new Vector3(0, 0, 1), 0)

  private target = new Vector3()
  private zoom = 1 // 像素 / 图纸单位
  private tilt = 0 // 度
  private dark = true
  private tween: Tween | null = null
  private raf = 0
  private dirty = true
  private disposed = false
  /** 初始视图：整张图适配在“没被悬浮面板盖住的区域”里（fitHome 更新） */
  private home: { x: number; y: number; zoom: number }
  /** 视图还停在初始位置（用户没拖、没缩放、没定位）：面板显示/隐藏时才跟着重新适配 */
  private atHome = true
  private readonly cleanups: (() => void)[] = []
  private pickHandler: ((name: string) => void) | null = null
  private hovered: LabelItem | null = null

  constructor(
    private readonly host: HTMLElement,
    geo: Geometry,
    private readonly insets: () => Insets = () => ({ left: 0, right: 0, top: 0, bottom: 0 }),
  ) {
    this.geo = geo
    this.renderer = new WebGLRenderer({ antialias: true })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    host.appendChild(this.renderer.domElement)
    this.renderer.domElement.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none'
    this.labelLayer.style.cssText = 'position:absolute;inset:0;overflow:hidden;pointer-events:none'
    host.appendChild(this.labelLayer)

    this.buildFills()
    this.buildLines()
    this.buildLabels()

    const [, , w, h] = geo.bbox
    this.target.set(w / 2, h / 2, 0)
    const vw = host.clientWidth || window.innerWidth
    const vh = host.clientHeight || window.innerHeight
    this.zoom = Math.min(vw / (w * 1.06), vh / (h * 1.1))
    this.home = { x: this.target.x, y: this.target.y, zoom: this.zoom }
    this.fitHome(false)

    this.bindEvents()
    this.resize()
    this.setTheme(true)
    const loop = (): void => {
      if (this.disposed) return
      this.raf = requestAnimationFrame(loop)
      this.frame(performance.now())
    }
    loop()
  }

  // ── 构建 ──────────────────────────────────────────────────────────

  /** 实心填充：每种颜色合成一个网格；孔洞按 Shape.holes 处理，坏多边形跳过不影响其他。 */
  private buildFills(): void {
    const toVec = (flat: number[]): Vector2[] => {
      const out: Vector2[] = []
      for (let i = 0; i + 1 < flat.length; i += 2) out.push(new Vector2(flat[i]!, flat[i + 1]!))
      return out
    }
    for (const [aci, fills] of Object.entries(this.geo.fills ?? {})) {
      const pos: number[] = []
      for (const f of fills) {
        try {
          const shape = new Shape(toVec(f.o))
          shape.holes = (f.h ?? []).map((h) => new Path(toVec(h)))
          const g = new ShapeGeometry(shape).toNonIndexed()
          pos.push(...(g.getAttribute('position').array as Float32Array))
          g.dispose()
        } catch {
          /* 退化多边形：跳过 */
        }
      }
      if (pos.length === 0) continue
      const geom = new BufferGeometry()
      geom.setAttribute('position', new Float32BufferAttribute(pos, 3))
      const mat = new MeshBasicMaterial({ color: 0xffffff, side: DoubleSide, transparent: true, depthWrite: false })
      const mesh = new Mesh(geom, mat)
      mesh.position.z = -1
      mesh.renderOrder = -1
      this.fillMats.push({ mat, aci })
      this.scene.add(mesh)
    }
  }

  /** 折线：按（图层，ACI 颜色）分组；ACI 1–6 保留作者的颜色，其余按图层角色上色。 */
  private buildLines(): void {
    for (const [layer, g] of Object.entries(this.geo.layers)) {
      const role = LAYER_ROLE[layer] ?? 'roadway'
      const z = role === 'facility' ? 1.5 : role === 'flow' ? 1 : 0
      const groups = new Map<number | null, number[]>()
      for (const l of g.lines) {
        const key = l.aci !== undefined && ACI_TINT[l.aci] ? l.aci : null
        let pos = groups.get(key)
        if (!pos) groups.set(key, (pos = []))
        const p = l.p
        for (let i = 0; i + 3 < p.length; i += 2) pos.push(p[i]!, p[i + 1]!, z, p[i + 2]!, p[i + 3]!, z)
        if (l.c && p.length >= 6) pos.push(p[p.length - 2]!, p[p.length - 1]!, z, p[0]!, p[1]!, z)
      }
      for (const [aci, pos] of groups) {
        const geom = new BufferGeometry()
        geom.setAttribute('position', new Float32BufferAttribute(pos, 3))
        const mat = new LineBasicMaterial({ color: 0xffffff })
        this.lineMats.push({ mat, role, aci })
        this.scene.add(new LineSegments(geom, mat))
      }
    }
  }

  private buildLabels(): void {
    for (const l of this.geo.labels) {
      if (l.kind === 'note' && l.text.length > 12) continue
      const el = document.createElement('div')
      el.textContent = l.text
      el.dataset.kind = l.kind
      el.className = 'rm-label'
      this.labelLayer.appendChild(el)
      const fs = LABEL_FONT[l.kind] ?? 10
      this.labels.push({ el, x: l.x, y: l.y, kind: l.kind, w: textWidth(l.text, fs) + 4, h: fs * 1.3 })
    }
    // 优先级高的先占位：名称 > 风量 > 断面 > 其他
    this.labels.sort((a, b) => (LABEL_PRIORITY[a.kind] ?? 9) - (LABEL_PRIORITY[b.kind] ?? 9))
  }

  // ── 控制 ──────────────────────────────────────────────────────────

  private bindEvents(): void {
    const el = this.renderer.domElement
    let drag: { x: number; y: number; right: boolean } | null = null
    const on = <K extends keyof HTMLElementEventMap>(t: K, f: (e: HTMLElementEventMap[K]) => void, o?: AddEventListenerOptions): void => {
      el.addEventListener(t, f as EventListener, o)
      this.cleanups.push(() => el.removeEventListener(t, f as EventListener))
    }
    let downAt: { x: number; y: number } | null = null
    on('pointerdown', (e) => {
      downAt = { x: e.clientX, y: e.clientY }
      drag = { x: e.clientX, y: e.clientY, right: e.button === 2 }
      el.setPointerCapture(e.pointerId)
      this.tween = null
      this.atHome = false
    })
    on('pointermove', (e) => {
      if (!drag) {
        this.hover(e.clientX, e.clientY)
        return
      }
      const dx = e.clientX - drag.x
      const dy = e.clientY - drag.y
      drag.x = e.clientX
      drag.y = e.clientY
      if (drag.right) this.setTilt(this.tilt - dy * 0.3)
      else {
        const cos = Math.max(Math.cos((this.tilt * Math.PI) / 180), 0.2)
        this.target.x -= dx / this.zoom
        this.target.y += dy / (this.zoom * cos)
        this.dirty = true
      }
    })
    on('pointerup', (e) => {
      const wasRight = drag?.right ?? false
      drag = null
      el.releasePointerCapture(e.pointerId)
      // 几乎没动就是点击（拖动平移不算）：点中名称标签就是“引用这个位置”
      if (!wasRight && downAt && Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) < 5) {
        const hit = this.hitName(e.clientX, e.clientY)
        if (hit && this.pickHandler) {
          this.flash(hit)
          this.pickHandler(hit.el.textContent ?? '')
        }
      }
      downAt = null
    })
    on('contextmenu', (e) => e.preventDefault())
    on(
      'wheel',
      (e) => {
        e.preventDefault()
        this.tween = null
        this.atHome = false
        const before = this.groundAt(e.clientX, e.clientY)
        this.zoom = Math.min(Math.max(this.zoom * Math.exp(-e.deltaY * 0.0015), this.home.zoom * 0.5), this.home.zoom * 40)
        this.updateCamera()
        const after = this.groundAt(e.clientX, e.clientY)
        if (before && after) this.target.add(before.sub(after)) // 缩放时保持鼠标下的点不动
        this.dirty = true
      },
      { passive: false },
    )
    const ro = new ResizeObserver(() => {
      this.resize()
      this.layoutChanged()
    })
    ro.observe(this.host)
    this.cleanups.push(() => ro.disconnect())
  }

  private groundAt(cx: number, cy: number): Vector3 | null {
    const r = this.renderer.domElement.getBoundingClientRect()
    this.ray.setFromCamera(new Vector2(((cx - r.left) / r.width) * 2 - 1, -((cy - r.top) / r.height) * 2 + 1), this.camera)
    const out = new Vector3()
    return this.ray.ray.intersectPlane(this.ground, out) ? out : null
  }

  private resize(): void {
    const w = this.host.clientWidth || window.innerWidth
    const h = this.host.clientHeight || window.innerHeight
    this.renderer.setSize(w, h, false)
    this.dirty = true
  }

  private updateCamera(): void {
    const w = this.host.clientWidth || window.innerWidth
    const h = this.host.clientHeight || window.innerHeight
    this.camera.left = -w / 2 / this.zoom
    this.camera.right = w / 2 / this.zoom
    this.camera.top = h / 2 / this.zoom
    this.camera.bottom = -h / 2 / this.zoom
    this.camera.updateProjectionMatrix()
    const t = (this.tilt * Math.PI) / 180
    const d = 2000
    this.camera.position.set(this.target.x, this.target.y - d * Math.sin(t), d * Math.cos(t))
    this.camera.up.set(0, 1, 0)
    this.camera.lookAt(this.target)
    this.camera.updateMatrixWorld()
  }

  // ── 点选 ──────────────────────────────────────────────────────────

  /** 点中名称标签时调用（点击，不是拖动）。 */
  onPick(handler: ((name: string) => void) | null): void {
    this.pickHandler = handler
  }

  /** 指针下面正在显示的名称标签（有重叠时取离中心最近的）；标签是 DOM 元素，用它们当前的屏幕矩形判断。 */
  private hitName(cx: number, cy: number): LabelItem | null {
    const pad = 5
    let best: LabelItem | null = null
    let bestD = Infinity
    for (const l of this.labels) {
      if (l.kind !== 'name' || l.el.style.display !== 'block') continue
      const r = l.el.getBoundingClientRect()
      if (cx < r.left - pad || cx > r.right + pad || cy < r.top - pad || cy > r.bottom + pad) continue
      const d = Math.hypot(cx - (r.left + r.right) / 2, cy - (r.top + r.bottom) / 2)
      if (d < bestD) {
        best = l
        bestD = d
      }
    }
    return best
  }

  private hover(cx: number, cy: number): void {
    const hit = this.hitName(cx, cy)
    if (hit === this.hovered) return
    this.hovered?.el.classList.remove('rm-hover')
    this.hovered = hit
    hit?.el.classList.add('rm-hover')
    this.renderer.domElement.style.cursor = hit && this.pickHandler ? 'pointer' : ''
  }

  private flash(l: LabelItem): void {
    l.el.classList.remove('rm-picked')
    void l.el.offsetWidth // 重新触发动画
    l.el.classList.add('rm-picked')
    window.setTimeout(() => l.el.classList.remove('rm-picked'), 900)
  }

  // ── 对外接口 ──────────────────────────────────────────────────────

  setTheme(dark: boolean): void {
    this.dark = dark
    const t = THEMES[dark ? 'dark' : 'light']
    this.renderer.setClearColor(new Color(t.bg), 1)
    for (const { mat, role, aci } of this.lineMats) mat.color.set(aci !== null ? ACI_TINT[aci]![dark ? 'dark' : 'light'] : t[role])
    for (const { mat, aci } of this.fillMats) {
      const st = fillStyle(t, aci)
      mat.color.set(st.color)
      mat.opacity = st.opacity
    }
    this.labelLayer.dataset.theme = dark ? 'dark' : 'light'
    this.dirty = true
  }

  setTilt(deg: number): void {
    this.tilt = Math.min(Math.max(deg, 0), 60)
    this.dirty = true
  }

  get tiltDeg(): number {
    return this.tilt
  }

  get isDark(): boolean {
    return this.dark
  }

  /** 按当前被面板盖住的区域重新算初始视图；animate 为真时飞过去。 */
  fitHome(animate: boolean): void {
    const [, , w, h] = this.geo.bbox
    const W = this.host.clientWidth || window.innerWidth
    const H = this.host.clientHeight || window.innerHeight
    const i = this.insets()
    const fw = Math.max(W - i.left - i.right, 200)
    const fh = Math.max(H - i.top - i.bottom, 150)
    const zoom = Math.min(fw / (w * 1.04), fh / (h * 1.06))
    const dx = (i.left + (W - i.right)) / 2 - W / 2
    const dy = (i.top + (H - i.bottom)) / 2 - H / 2
    this.home = { x: w / 2 - dx / zoom, y: h / 2 + dy / zoom, zoom }
    if (animate) this.tween = { t0: performance.now(), dur: 600, from: { x: this.target.x, y: this.target.y, zoom: this.zoom }, to: this.home }
    else {
      this.target.set(this.home.x, this.home.y, 0)
      this.zoom = zoom
    }
    this.atHome = true
    this.dirty = true
  }

  /** 面板显示/隐藏、窗口尺寸变化之后调用：停在初始位置的视图跟着重新适配，用户已经动过的不打扰。 */
  layoutChanged(): void {
    if (this.atHome) this.fitHome(true)
  }

  reset(): void {
    this.fitHome(true)
    this.setTilt(0)
  }

  /** 名称在图上的位置；找不到返回 null。 */
  locate(name: string): { x: number; y: number } | null {
    const ids = this.geo.names[name]
    const l = ids && this.geo.labels.find((t) => t.id === ids[0])
    return l ? { x: l.x, y: l.y } : null
  }

  focus(name: string, zoom?: number): boolean {
    const p = this.locate(name)
    if (!p) return false
    this.flyTo(p.x, p.y, this.home.zoom * (zoom ?? 5), true)
    return true
  }

  /**
   * 飞到 (x, y)。avoidPanels 时，目标点落在没被悬浮面板盖住的区域中心，而不是整个窗口的中心。
   * 屏幕上偏移 (dx, dy) 像素，对应世界里 (dx/zoom, dy/(zoom·cos 倾斜)) 的反向位移。
   */
  private flyTo(x: number, y: number, zoom: number, avoidPanels: boolean): void {
    this.atHome = false
    let tx = x
    let ty = y
    if (avoidPanels) {
      const w = this.host.clientWidth || window.innerWidth
      const h = this.host.clientHeight || window.innerHeight
      const i = this.insets()
      const dx = (i.left + (w - i.right)) / 2 - w / 2
      const dy = (i.top + (h - i.bottom)) / 2 - h / 2
      const cos = Math.max(Math.cos((this.tilt * Math.PI) / 180), 0.2)
      tx = x - dx / zoom
      ty = y + dy / (zoom * cos)
    }
    this.tween = { t0: performance.now(), dur: 900, from: { x: this.target.x, y: this.target.y, zoom: this.zoom }, to: { x: tx, y: ty, zoom } }
  }

  mark(m: MapMark): boolean {
    const p = this.locate(m.name)
    if (!p) return false
    this.unmark(m.id)
    const color = LEVEL_COLOR[m.level] ?? LEVEL_COLOR.info!
    const ring = new Mesh(new RingGeometry(0.78, 1, 48), new MeshBasicMaterial({ color, transparent: true, opacity: 0.9 }))
    ring.position.set(p.x, p.y, 2)
    const pinGeom = new BufferGeometry()
    pinGeom.setAttribute('position', new Float32BufferAttribute([0, 0, 0, 0, 0, 1], 3)) // 单位竖线，位置和高度每帧按缩放设置
    const pin = new LineSegments(pinGeom, new LineBasicMaterial({ color }))
    pin.position.set(p.x, p.y, 2)
    const tag = document.createElement('div')
    tag.className = 'rm-mark'
    tag.dataset.level = m.level
    tag.textContent = m.text ? `${m.name} · ${m.text}` : m.name
    this.labelLayer.appendChild(tag)
    this.scene.add(ring, pin)
    this.marks.set(m.id, { data: m, ring, pin, tag, x: p.x, y: p.y, born: performance.now() })
    this.dirty = true
    return true
  }

  unmark(id: string): void {
    const m = this.marks.get(id)
    if (!m) return
    this.scene.remove(m.ring, m.pin)
    m.ring.geometry.dispose()
    m.pin.geometry.dispose()
    m.tag.remove()
    this.marks.delete(id)
    this.dirty = true
  }

  // ── 图元（撤人路线、污染巷道、人员分组：只显示服务端按记录生成的内容） ──

  private removeFigure(id: string): void {
    const f = this.figs.get(id)
    if (!f) return
    if (f.line) {
      this.scene.remove(f.line)
      f.line.geometry.dispose()
      ;(f.line.material as LineMaterial).dispose()
    }
    f.el?.remove()
    this.figs.delete(id)
    this.dirty = true
  }

  private setFigure(d: MapFigure): void {
    this.removeFigure(d.id)
    const stale = !!d.stale
    if (d.type === 'line' && d.points && d.points.length >= 2) {
      const contaminated = d.kind === 'contaminated'
      const color = stale ? FIG_GREY : contaminated ? contaminationColor(this.contaminationRel(d)) : '#35c46a'
      const geom = new LineGeometry()
      geom.setPositions(d.points.flatMap(([x, y]) => [x, y, contaminated ? 1 : 1.5]))
      const mat = new LineMaterial({ color: new Color(color).getHex(), linewidth: contaminated ? 7 : 4, transparent: true, opacity: stale ? 0.45 : contaminated ? 0.6 : 0.95, dashed: !contaminated, dashSize: 14, gapSize: 9, worldUnits: false, depthWrite: false })
      mat.resolution.set(this.host.clientWidth || window.innerWidth, this.host.clientHeight || window.innerHeight)
      const line = new Line2(geom, mat)
      if (!contaminated) line.computeLineDistances()
      line.renderOrder = contaminated ? 1 : 2
      this.scene.add(line)
      const mid = d.points[Math.floor(d.points.length / 2)]!
      const el = d.text ? this.figLabel(d, `rm-fig rm-fig-${d.kind}`) : undefined
      this.figs.set(d.id, { data: d, line, el, x: mid[0], y: mid[1] })
    } else if ((d.type === 'group' || d.type === 'target') && d.at) {
      const el = this.figLabel(d, `rm-fig rm-fig-${d.type} rm-fig-${d.kind}`)
      this.figs.set(d.id, { data: d, el, x: d.at[0], y: d.at[1] })
    }
    this.dirty = true
  }

  /** 污染巷道到达时间在本批里的相对位置（0 最早，1 最晚），只用来上色 */
  private contaminationRel(d: MapFigure): number {
    const vs = [...this.figs.values(), { data: d }].map((f) => f.data).filter((f) => f.kind === 'contaminated' && typeof f.value === 'number').map((f) => f.value as number)
    if (vs.length < 2 || typeof d.value !== 'number') return 0
    const lo = Math.min(...vs)
    const hi = Math.max(...vs)
    return hi === lo ? 0 : (d.value - lo) / (hi - lo)
  }

  private figLabel(d: MapFigure, cls: string): HTMLDivElement {
    const el = document.createElement('div')
    el.className = cls + (d.stale ? ' rm-fig-stale' : '')
    if (d.type === 'group') {
      el.textContent = d.kind === 'blocked' ? `${d.value ?? ''} 人 · 需人工研判` : `${d.value ?? ''} 人`
      if (d.text) el.title = d.text
    } else {
      el.textContent = d.type === 'target' ? `${d.kind === 'refuge' ? '避难' : '出口'} ${d.text ?? ''}`.trim() : d.text ?? ''
    }
    this.labelLayer.appendChild(el)
    return el
  }

  clearMarks(): void {
    for (const id of [...this.marks.keys()]) this.unmark(id)
  }

  applySnapshot(s: MapSnapshot): void {
    this.clearMarks()
    for (const m of s.marks) this.mark(m)
    for (const id of [...this.figs.keys()]) this.removeFigure(id)
    for (const f of s.figures ?? []) this.setFigure(f)
    if (s.tilt !== null) this.setTilt(s.tilt)
    if (s.focus) this.focus(s.focus.name, s.focus.zoom)
  }

  applyCommand(c: MapCommand): void {
    if (c.op === 'focus' && c.name) this.focus(c.name, c.zoom)
    else if (c.op === 'mark' && c.name) {
      this.mark({ id: c.id ?? c.name, name: c.name, level: c.level ?? 'info', text: c.text ?? '' })
      if (c.zoom !== undefined) this.focus(c.name, c.zoom)
    } else if (c.op === 'unmark' && c.id) this.unmark(c.id)
    else if (c.op === 'clear') {
      this.clearMarks()
      this.reset()
    } else if (c.op === 'view' && c.tilt !== undefined) this.setTilt(c.tilt)
    else if (c.op === 'reset') this.reset()
    else if (c.op === 'figure' && c.fig) this.setFigure(c.fig)
    else if (c.op === 'unfigure' && c.fig) this.removeFigure(c.fig.id)
  }

  get figureCount(): number {
    return this.figs.size
  }

  get markCount(): number {
    return this.marks.size
  }

  /** 当前显示的标签数（测试和调试用）。 */
  get visibleLabelCount(): number {
    return this.labels.filter((l) => l.el.style.display === 'block').length
  }

  // ── 渲染循环 ──────────────────────────────────────────────────────

  private frame(now: number): void {
    if (document.hidden) return
    let moved = false
    if (this.tween) {
      const k = Math.min((now - this.tween.t0) / this.tween.dur, 1)
      const e = ease(k)
      const { from, to } = this.tween
      this.target.x = from.x + (to.x - from.x) * e
      this.target.y = from.y + (to.y - from.y) * e
      this.zoom = from.zoom * Math.pow(to.zoom / from.zoom, e) // 缩放按对数插值，视觉上匀速
      if (k >= 1) this.tween = null
      moved = true
    }
    let routes = false
    for (const f of this.figs.values()) {
      if (!f.line) continue
      const mat = f.line.material as LineMaterial
      mat.resolution.set(this.host.clientWidth || window.innerWidth, this.host.clientHeight || window.innerHeight)
      if (f.data.kind === 'route' && !f.data.stale) {
        mat.dashOffset = -(now / 40) % 23 // 虚线沿路线方向流动
        routes = true
      }
    }
    const pulsing = this.marks.size > 0 || routes
    for (const m of this.marks.values()) {
      const s = (RING_PX / this.zoom) * (1 + 0.3 * Math.sin((now - m.born) / 260))
      m.ring.scale.set(s, s, 1)
      m.pin.scale.set(1, 1, PIN_PX / this.zoom)
    }
    if (!this.dirty && !moved && !pulsing) return
    const relayout = this.dirty || moved
    this.dirty = false
    this.updateCamera()
    this.renderer.render(this.scene, this.camera)
    if (relayout) this.placeLabels()
  }

  /**
   * 标签布局：标记的标签先占位，再按优先级（名称 > 风量 > 断面 > 其他）逐个放，
   * 和已放的标签重叠就隐藏；放大后空间变大，被隐藏的会自然出现。风量等小字只在放大到一定程度才参与。
   */
  private placeLabels(): void {
    const w = this.host.clientWidth || window.innerWidth
    const h = this.host.clientHeight || window.innerHeight
    const v = new Vector3()
    const rel = this.zoom / this.home.zoom
    const allowed = (kind: string): boolean => kind === 'name' || (kind === 'flow' && rel > 2.2) || (kind === 'section' && rel > 4) || (kind === 'note' && rel > 5)
    const placed: [number, number, number, number][] = []

    for (const m of this.marks.values()) {
      v.set(m.x, m.y, 2 + PIN_PX / this.zoom).project(this.camera)
      const px = (v.x * 0.5 + 0.5) * w
      const py = (-v.y * 0.5 + 0.5) * h
      m.tag.style.transform = `translate(${px.toFixed(1)}px,${py.toFixed(1)}px)`
      placed.push([px - 4, py - 30, m.tag.offsetWidth || 120, m.tag.offsetHeight || 22])
    }
    for (const f of this.figs.values()) {
      if (!f.el) continue
      v.set(f.x, f.y, 3).project(this.camera)
      const px = (v.x * 0.5 + 0.5) * w
      const py = (-v.y * 0.5 + 0.5) * h
      // 与已放的标记标签、其他徽标重叠时，上下错开找空位；都不行就放在原位（不隐藏：撤人信息不能因为拥挤消失）
      const fw = f.el.offsetWidth || 50
      const fh = f.el.offsetHeight || 20
      let best: [number, number] = [0, 0]
      for (const [dx, dy] of [[0, 0], [0, 22], [0, -22], [0, 44], [0, -44], [fw + 8, 0], [-fw - 8, 0]] as [number, number][]) {
        const r: [number, number, number, number] = [px - 4 + dx, py - 12 + dy, fw, fh]
        if (!placed.some((q) => rectsOverlap(r, q))) {
          best = [dx, dy]
          break
        }
      }
      f.el.style.transform = `translate(${(px + best[0]).toFixed(1)}px,${(py + best[1]).toFixed(1)}px)`
      placed.push([px - 4 + best[0], py - 12 + best[1], fw, fh])
    }
    for (const l of this.labels) {
      if (!allowed(l.kind)) {
        l.el.style.display = 'none'
        continue
      }
      v.set(l.x, l.y, 0).project(this.camera)
      const px = (v.x * 0.5 + 0.5) * w
      const py = (-v.y * 0.5 + 0.5) * h
      const rect: [number, number, number, number] = [px + 4, py - 6, l.w, l.h]
      const onScreen = px > -80 && px < w + 80 && py > -20 && py < h + 20
      if (!onScreen || placed.some((r) => rectsOverlap(rect, r))) {
        l.el.style.display = 'none'
        continue
      }
      placed.push(rect)
      l.el.style.display = 'block'
      l.el.style.transform = `translate(${px.toFixed(1)}px,${py.toFixed(1)}px)`
    }
  }

  dispose(): void {
    this.disposed = true
    cancelAnimationFrame(this.raf)
    this.cleanups.forEach((f) => f())
    this.renderer.dispose()
    this.host.replaceChildren()
  }
}
