/**
 * rescue-map 浏览器端（docs/design-v4-map.md）：下层是通风图，上层是自绘的大屏界面（顶栏、左栏、右栏）
 * 加 DSH 的会话组件（底部）。任何一步失败（几何、WebGL）都退回 DSH 原界面，救援系统不能因为地图而不可用。
 */
import { connect, loadGeometry } from './channel.ts'
import type { Channel } from './channel.ts'
import { mountHud, panelInsets, restoreNative } from './hud.ts'
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

function mount(): () => void {
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

      let last: StateData | null = null
      let lastOk = 0
      const refresh = async (): Promise<void> => {
        try {
          last = await fetchState()
          lastOk = Date.now()
          hud?.setState(last, 0, false)
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

export function apply(ctx: { effect?: (fn: () => () => void, name?: string) => unknown }): void {
  if (typeof ctx.effect === 'function') ctx.effect(mount, 'rescue-map: map layer')
  else mount()
}
