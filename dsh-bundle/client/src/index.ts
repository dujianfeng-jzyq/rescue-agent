/**
 * rescue-map 浏览器端（docs/design-v4-map.md）：下层是通风图，上层是自绘的大屏界面（顶栏、左栏、右栏）
 * 加 DSH 的会话组件（底部）。任何一步失败（几何、WebGL）都退回 DSH 原界面，救援系统不能因为地图而不可用。
 */
import { mountApprovalStyle } from './approval-style.ts'
import { connect, loadGeometry } from './channel.ts'
import { dutyFollower } from './duty.ts'
import type { UiWorkspace } from './duty.ts'
import type { Channel } from './channel.ts'
import { mountHud, panelInsets, restoreNative, SLIDE_MS } from './hud.ts'
import { insertIntoComposer, referenceText } from './reference.ts'
import type { Hud } from './hud.ts'
import { MapView } from './scene.ts'
import type { StateData } from './state-types.ts'

export const inject: string[] = []

const POLL_MS = 3000

async function fetchState(): Promise<StateData> {
  const r = await fetch(new URL('rescue-map/state', document.baseURI), { credentials: 'same-origin', cache: 'no-store' })
  if (!r.ok) throw new Error(`状态接口 ${r.status}`)
  return (await r.json()) as StateData
}

/**
 * 关闭地图界面、保持 DSH 原界面：评测、故事线、边界脚本靠 DSH 原界面的按钮（“New Session”等）操作，
 * 它们在页面脚本里设 localStorage['rescue-map:disabled'] = '1'。操作员也可以在浏览器控制台里设这个值关掉地图。
 */
function disabled(): boolean {
  try {
    return localStorage.getItem('rescue-map:disabled') === '1'
  } catch {
    return false
  }
}

/** 本标签页上次见到的 DSH 启动编号（传参时写入） */
function seenBoot(value?: string): string {
  try {
    if (value !== undefined) sessionStorage.setItem('rescue-map:boot', value)
    return sessionStorage.getItem('rescue-map:boot') ?? ''
  } catch {
    return value ?? ''
  }
}

interface ClientContext {
  effect?: (fn: () => () => void, name?: string) => unknown
  get?: (name: string) => unknown
}

function mount(ctx: ClientContext): () => void {
  if (disabled()) return () => {}
  const host = document.createElement('div')
  host.id = 'rescue-map-host'
  document.body.prepend(host)
  let view: MapView | null = null
  let channel: Channel | null = null
  let hud: Hud | null = null
  let poll = 0
  let gone = false

  void (async () => {
    try {
      const geo = await loadGeometry()
      if (gone) return
      view = new MapView(host, geo, panelInsets)
      view.setTheme(true) // 大屏固定深色
      hud = mountHud({
        legend: () => geo.legend,
        title: () => geo.title,
        layoutChanged: () => view?.layoutChanged(),
        focus: (name) => view?.focus(name, 4),
        fireSpreadTime: (t) => view?.setFireSpreadTime(t),
        tilt: () => view!.tiltDeg,
        setTilt: (d) => view!.setTilt(d),
        reset: () => view!.reset(),
      })
      view.fitHome(false) // 面板已经挂上，按没被盖住的区域重新适配
      // 点选引用：点地图上的名称，往输入框里插“【地图：名称】”
      view.onPick((name) => {
        hud?.showChat()
        // 聊天区刚显示出来时输入框要等一帧才有尺寸
        requestAnimationFrame(() => {
          if (insertIntoComposer(referenceText(name)) === 'inserted') hud?.toast(`已引用：${name}`)
          else {
            void navigator.clipboard?.writeText(referenceText(name)).catch(() => undefined)
            hud?.toast(`输入框现在不可用（正在等确认或回答？），已复制：${referenceText(name)}`)
          }
        })
      })
      channel = connect(
        (s) => view?.applySnapshot(s),
        (c) => view?.applyCommand(c),
        (ok) => hud?.setConnected(ok),
      )

      // 监听器的告警投递到值班会话时自动切过去（打开页面时也是），不用操作员去会话列表里找
      const followDuty = dutyFollower({
        workspace: () => {
          try {
            return ctx.get?.('uiWorkspace') as UiWorkspace | undefined
          } catch {
            return undefined
          }
        },
        // 打开页面时只悄悄切过去，保持全屏地图；投递告警时把对话区拉出来
        switched: (reason) => {
          if (reason !== 'delivery') return
          hud?.showChat()
          hud?.toast('监听器告警已投递给智能体，已切到值班会话')
        },
      })

      // 来了新告警（最近一条告警的编号变了）：退出全屏地图，两侧面板滑出来，镜头飞到事件所在的工作面。
      // 第一次只记下已有的告警（刚打开页面时不动）。飞的目标按面板展开后的空白区域算，和面板滑动同时进行
      let seenAlert: string | null | undefined
      const watchAlert = (s: StateData): void => {
        const inc = s.incident
        const key = inc?.latestAlertId ? `${inc.id}:${inc.latestAlertId}` : null
        if (seenAlert === undefined) {
          seenAlert = key
          return
        }
        if (!inc || key === null || key === seenAlert) return
        seenAlert = key
        const wasFull = hud?.setFullscreen(false) ?? false
        // 面板滑完再飞：两件事同时做，配置弱的机器上都会掉帧（飞的目标本来就按面板展开后的位置算）
        const anchor = inc.anchor
        if (anchor) window.setTimeout(() => view?.focus(anchor, 3), wasFull ? SLIDE_MS : 0)
        const level = inc.level === 'none' ? '' : (inc.levelName ?? '')
        hud?.toast(`收到预警：${[inc.panel, level, inc.stageName].filter(Boolean).join(' · ') || inc.summary || inc.id}`, 'alert')
      }

      let last: StateData | null = null
      let lastOk = 0
      const refresh = async (): Promise<void> => {
        try {
          last = await fetchState()
          lastOk = Date.now()
          // DSH 重启过（新一轮演示）：页面上的会话已经不存在，刷新页面。DSH 重连后会重新挂载本插件，
          // 所以上次见到的启动编号存在 sessionStorage 里（只在本标签页），不能放在闭包里
          if (last.boot && last.boot !== seenBoot()) {
            const before = seenBoot()
            seenBoot(last.boot)
            if (before) return location.reload()
          }
          hud?.setState(last, 0, false)
          watchAlert(last)
          followDuty(last)
        } catch {
          hud?.setState(last, lastOk ? Math.round((Date.now() - lastOk) / 1000) : 0, true)
        }
      }
      await refresh()
      poll = window.setInterval(() => void refresh(), POLL_MS)
      ;(window as unknown as { __rescueMap?: unknown }).__rescueMap = view // 调试用
    } catch (e) {
      console.warn('[rescue-map] 地图不可用，保持 DSH 原界面：', e)
      restoreNative()
      host.remove()
    }
  })()

  return () => {
    gone = true
    window.clearInterval(poll)
    channel?.close()
    view?.dispose()
    hud?.dispose()
    host.remove()
  }
}

export function apply(ctx: ClientContext): void {
  // 确认卡片的排版与地图无关：评测关掉地图界面时也保留
  if (typeof ctx.effect === 'function') ctx.effect(mountApprovalStyle, 'rescue: approval card style')
  else mountApprovalStyle()
  if (typeof ctx.effect === 'function') ctx.effect(() => mount(ctx), 'rescue-map: map layer')
  else mount(ctx)
}
