/**
 * 大屏界面（docs/design-v4-map.md 第 6 节）：地图在最底层，上面是
 *   顶栏（标题、时间、状态、工具）、左栏（矿井信息）、右栏（智能体信息）——这三块是自绘的（panels.ts）；
 *   底部是 DSH 的会话组件（消息、输入框、人工确认、提问卡片原样保留），只换皮肤、改位置。
 * DSH 原来的左侧会话栏改成按钮呼出的抽屉，右栏（文件预览等）打开时浮在最上面。
 *
 * 选择器依赖 DSH 0.2.1-rc.4 的 DOM（类名带哈希前缀，用 [class$="_xxx"] 匹配；右栏用 data 属性），
 * 版本已锁定；升级 DSH 时要重新核对（验证清单 #26、#28、#29）。
 *
 * 整个界面固定用深色：强制 body 带 data-ds-dark-theme，DSH 的组件就用它自己的深色变量，
 * 不依赖用户在 DSH 设置里选了什么主题。
 */
import { renderLegend } from './legend.ts'
import { mountPanels } from './panels.ts'
import type { PanelsHandle } from './panels.ts'
import { h } from './panels.ts'
import type { Insets } from './scene.ts'
import type { StateData } from './state-types.ts'
import type { Legend } from './types.ts'

const STYLE_ID = 'rescue-map-style'
const HUD_ID = 'rescue-hud'
const ON = 'html.rm-on'
const WRAP = '#root > div'
const FRAME = `${WRAP} > [class$="_frame"]`
const SIDEBAR = `${FRAME} > [class$="_sidebarCol"]`
const CENTER = `${FRAME} > [class$="_centerCol"]`
// 右栏不是 frame 的直接子元素（中间隔着 display: contents 的包装），宽度是内联样式，所以用后代选择器并加 !important
const RIGHT = `${FRAME} [data-sidebar-right-panel]`
// 右栏的外层容器原来靠网格撑开，改成块布局后高度为 0 且 position: relative，会成为右栏的包含块
const RIGHTCOL = `${FRAME} > [class$="_rightbarCol"]`
const OVERLAY = `${FRAME} > [class$="_overlayLayer"]`

const CARD = `
  background: linear-gradient(180deg, rgba(14, 30, 52, 0.82), rgba(7, 16, 30, 0.78)) !important;
  backdrop-filter: blur(14px) saturate(1.2);
  -webkit-backdrop-filter: blur(14px) saturate(1.2);
  border: 1px solid rgba(90, 160, 255, 0.24) !important;
  border-radius: 14px !important;
  box-shadow: 0 12px 44px rgba(0, 0, 0, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.05);
  overflow: hidden;
`

/** DSH 组件用的底色变量换成大屏的海军蓝（只在这几个容器里生效，不影响 DSH 其他地方） */
const NAVY = `
  --dsw-alias-bg-base: transparent;
  --dsw-alias-bg-layer-1: rgba(22, 44, 76, 0.55);
  --dsw-alias-bg-layer-2: rgba(30, 58, 96, 0.6);
  --dsw-alias-bg-layer-3: rgba(40, 74, 120, 0.66);
  --dsw-specific-input-major: rgba(12, 28, 52, 0.88);
  --dsw-specific-sidebar-fill: transparent;
`

const CSS = `
${ON}, ${ON} body { background: #0b1522 !important; color-scheme: dark; }
#rescue-map-host { position: fixed; inset: 0; z-index: 0; }
${ON} { --rm-l: 372px; --rm-r: 372px; --rm-top: 68px; }
${ON}.rm-hide-left { --rm-l: 16px; }
${ON}.rm-hide-right { --rm-r: 16px; }
${ON} #root { position: relative; z-index: 2; height: 100%; pointer-events: none; }
${ON} ${WRAP} { height: 100%; background: transparent !important; pointer-events: none; }
${ON} ${FRAME} { display: block !important; position: relative; height: 100%; background: transparent !important; pointer-events: none; }
${ON} ${RIGHTCOL} { display: contents !important; }
${ON} ${OVERLAY} { pointer-events: none; }
${ON} ${OVERLAY} > * { pointer-events: auto; }

/* 底部：DSH 的会话组件 */
${ON} ${CENTER} { position: absolute !important; left: var(--rm-l); right: var(--rm-r); bottom: 16px; height: min(40vh, 460px); pointer-events: auto; ${NAVY} ${CARD} }
${ON} ${CENTER} > * { background: transparent !important; }
${ON}.rm-hide-chat ${CENTER} { display: none !important; }
/* DSH 的右栏（文件预览）打开时，聊天区让出位置，不被它盖住 */
${ON} ${FRAME}:not([data-rightbar-collapsed]) > [class$="_centerCol"] { right: calc(min(520px, 100vw - 32px) + 32px); }

/* DSH 的会话列表：按钮呼出的抽屉 */
${ON} ${SIDEBAR} { display: none !important; }
${ON}.rm-drawer ${SIDEBAR} { display: block !important; position: absolute !important; left: 16px; top: var(--rm-top); bottom: 16px; width: 320px; z-index: 30; pointer-events: auto; ${NAVY} ${CARD} }
${ON}.rm-drawer ${SIDEBAR} > * { background: transparent !important; }

/* DSH 的右栏（文件预览、终端等）：打开时浮在最上面，收起时不占位 */
${ON} ${FRAME}[data-rightbar-collapsed] [data-sidebar-right-panel] { display: none !important; }
${ON} ${RIGHT} { position: absolute !important; right: 16px; top: var(--rm-top); bottom: 16px; width: min(520px, calc(100vw - 32px)) !important; z-index: 40; pointer-events: auto; ${NAVY} ${CARD} }

/* 自绘部分 */
#${HUD_ID} { position: fixed; inset: 0; z-index: 1; pointer-events: none; color: #dbe8f5; font: 13px/1.45 system-ui, -apple-system, 'Segoe UI', 'Noto Sans CJK SC', 'PingFang SC', sans-serif; }
#${HUD_ID} > * { pointer-events: auto; }
.rm-header {
  position: absolute; top: 0; left: 0; right: 0; height: 56px; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 0 16px;
  background: linear-gradient(180deg, rgba(8, 18, 34, 0.92), rgba(8, 18, 34, 0.55)); border-bottom: 1px solid rgba(90, 160, 255, 0.22);
  box-shadow: 0 6px 30px rgba(0, 0, 0, 0.35); backdrop-filter: blur(10px);
}
.rm-brand { display: flex; align-items: center; gap: 10px; min-width: 0; }
.rm-logo { display: grid; place-items: center; filter: drop-shadow(0 0 6px rgba(79, 209, 255, 0.55)); }
.rm-title { display: flex; flex-direction: column; min-width: 0; }
.rm-title b { font-size: 17px; letter-spacing: 0.12em; color: #eaf4ff; text-shadow: 0 0 12px rgba(79, 209, 255, 0.45); white-space: nowrap; }
.rm-title span { font-size: 11px; color: #7fb2e6; letter-spacing: 0.06em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rm-clock { text-align: center; }
.rm-clock-main { font: 600 18px/1.1 ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace; color: #4fd1ff; text-shadow: 0 0 10px rgba(79, 209, 255, 0.5); white-space: nowrap; }
.rm-clock-sub { font-size: 11px; color: #7f93ab; margin-top: 2px; }
.rm-header-right { display: flex; align-items: center; gap: 12px; min-width: 0; }
.rm-chips { display: flex; gap: 6px; }
.rm-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; white-space: nowrap; background: rgba(120, 160, 210, 0.12); border: 1px solid rgba(120, 160, 210, 0.2); }
.rm-chip i { width: 7px; height: 7px; border-radius: 50%; background: #7f93ab; }
.rm-chip[data-tone="ok"] i { background: #35c46a; box-shadow: 0 0 8px #35c46a; }
.rm-chip[data-tone="warn"] i { background: #f5c518; box-shadow: 0 0 8px #f5c518; }
.rm-chip[data-tone="bad"] { color: #ff8f8f; border-color: rgba(255, 77, 79, 0.4); }
.rm-chip[data-tone="bad"] i { background: #ff4d4f; box-shadow: 0 0 8px #ff4d4f; }
.rm-tools { display: flex; gap: 4px; }
.rm-tools button {
  font: inherit; font-size: 12px; color: #cfe1f5; cursor: pointer; padding: 6px 11px; border-radius: 8px; white-space: nowrap;
  border: 1px solid rgba(120, 160, 210, 0.22); background: rgba(120, 160, 210, 0.08);
}
.rm-tools button:hover { background: rgba(79, 209, 255, 0.16); border-color: rgba(79, 209, 255, 0.5); }
.rm-tools button[aria-pressed="false"] { opacity: 0.5; }
.rm-tools button[aria-pressed="true"] { border-color: rgba(79, 209, 255, 0.6); color: #4fd1ff; }

.rm-card { ${CARD} }
.rm-left, .rm-right { box-sizing: border-box; position: absolute; top: var(--rm-top); bottom: 16px; width: 340px; overflow-y: auto; padding: 6px 14px 14px; scrollbar-width: thin; scrollbar-color: rgba(120, 160, 210, 0.35) transparent; }
.rm-left { left: 16px; }
.rm-right { right: 16px; }
html.rm-hide-left .rm-left, html.rm-hide-right .rm-right { display: none; }
.rm-sec { padding: 10px 0 4px; border-bottom: 1px solid rgba(120, 160, 210, 0.12); }
.rm-sec:last-child { border-bottom: 0; }
.rm-sec h3 { margin: 0 0 8px; font-size: 12px; font-weight: 600; letter-spacing: 0.16em; color: #7fb2e6; display: flex; align-items: center; gap: 8px; }
.rm-sec h3::before { content: ''; width: 3px; height: 12px; border-radius: 2px; background: #4fd1ff; box-shadow: 0 0 8px #4fd1ff; }
.rm-empty { color: #7f93ab; padding: 6px 0 8px; font-size: 12px; }
.rm-note { margin: 6px 0 4px; color: #7f93ab; font-size: 11px; }
.rm-summary { margin: 8px 0 4px; color: #b9cbe0; font-size: 12px; }

.rm-situation { padding: 4px 0 6px; }
.rm-level { font-size: 30px; font-weight: 700; letter-spacing: 0.2em; color: var(--lv); text-shadow: 0 0 18px var(--lv); margin-bottom: 4px; }
.rm-kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 0; }
.rm-kv dt { color: #7f93ab; font-size: 12px; }
.rm-kv dd { margin: 0; text-align: right; font-size: 12px; color: #dbe8f5; overflow-wrap: anywhere; }
.rm-mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11px !important; }
.rm-badges { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.rm-badge { padding: 2px 8px; border-radius: 6px; font-size: 11px; }
.rm-badge[data-tone="warn"] { background: rgba(245, 197, 24, 0.15); color: #f5c518; border: 1px solid rgba(245, 197, 24, 0.4); }
.rm-badge[data-tone="bad"] { background: rgba(255, 77, 79, 0.15); color: #ff8f8f; border: 1px solid rgba(255, 77, 79, 0.45); }

.rm-sensors { display: flex; flex-direction: column; gap: 8px; }
.rm-sensor { padding: 6px 8px; border-radius: 8px; background: rgba(120, 160, 210, 0.06); border: 1px solid rgba(120, 160, 210, 0.12); }
.rm-sensor-name { font-size: 12px; color: #b9cbe0; margin-bottom: 4px; }
.rm-sensor-vals { display: grid; grid-template-columns: repeat(auto-fit, minmax(92px, 1fr)); gap: 4px 10px; }
.rm-val { display: flex; align-items: baseline; gap: 5px; white-space: nowrap; }
.rm-val span { color: #7f93ab; font-size: 11px; min-width: 30px; }
.rm-val b { font: 600 14px ui-monospace, Menlo, Consolas, monospace; color: #7ad9a1; }
.rm-val small { color: #7f93ab; font-size: 11px; }
.rm-sensor[data-anchor], .rm-alarms li[data-anchor], .rm-fire li { cursor: pointer; }
.rm-sensor[data-anchor]:hover, .rm-sensor[data-anchor]:focus-visible { border-color: rgba(79, 209, 255, 0.55); background: rgba(79, 209, 255, 0.1); outline: none; }
.rm-alarms { list-style: none; margin: 0 0 6px; padding: 0; }
.rm-alarms li { display: grid; grid-template-columns: auto 1fr auto auto; align-items: center; gap: 8px; padding: 6px 4px; border-bottom: 1px solid rgba(120, 160, 210, 0.1); font-size: 12px; }
.rm-alarms li:hover { background: rgba(255, 77, 79, 0.08); }
.rm-alarms i { width: 8px; height: 8px; border-radius: 50%; background: #f5c518; box-shadow: 0 0 8px #f5c518; }
.rm-alarms li[data-level="alarm"] i, .rm-alarms li[data-level="critical"] i { background: #ff4d4f; box-shadow: 0 0 8px #ff4d4f; }
.rm-alarm-name { color: #b9cbe0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rm-alarms b { font: 600 12px ui-monospace, Menlo, Consolas, monospace; color: #ff8f8f; white-space: nowrap; }
.rm-alarms time { color: #7f93ab; font: 11px ui-monospace, Menlo, Consolas, monospace; }
.rm-val[data-q]:not([data-q="ok"]) b, .rm-val[data-q]:not([data-q="ok"]) small { color: #f5c518; }

.rm-timeline { list-style: none; margin: 0; padding: 0 0 0 12px; border-left: 1px solid rgba(120, 160, 210, 0.25); }
.rm-timeline li { position: relative; padding: 0 0 10px 8px; display: grid; grid-template-columns: auto 1fr; column-gap: 8px; }
.rm-timeline li::before { content: ''; position: absolute; left: -16px; top: 5px; width: 7px; height: 7px; border-radius: 50%; background: var(--c); box-shadow: 0 0 8px var(--c); }
.rm-timeline time, .rm-list time { color: #7f93ab; font: 11px ui-monospace, Menlo, Consolas, monospace; grid-row: 1; }
.rm-timeline b { color: var(--c); font-weight: 600; font-size: 12px; }
.rm-timeline span { grid-column: 1 / -1; color: #b9cbe0; font-size: 12px; overflow-wrap: anywhere; }
.rm-list { list-style: none; margin: 0; padding: 0; }
.rm-list li { display: flex; align-items: center; gap: 8px; padding: 5px 0; border-bottom: 1px solid rgba(120, 160, 210, 0.1); font-size: 12px; }
.rm-list li em { margin-left: auto; font-style: normal; color: #ff8a1f; font-size: 11px; }
.rm-records code { font: 11px ui-monospace, Menlo, Consolas, monospace; color: #9fc4ee; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rm-records time { margin-left: auto; }
.rm-kind { padding: 1px 6px; border-radius: 4px; background: rgba(79, 209, 255, 0.14); color: #4fd1ff; font-size: 11px; white-space: nowrap; }

.rm-agent { display: flex; align-items: center; gap: 10px; font-size: 16px; padding: 2px 0 4px; }
.rm-agent i { width: 10px; height: 10px; border-radius: 50%; background: #7f93ab; }
.rm-agent[data-tone="ok"] i { background: #35c46a; box-shadow: 0 0 10px #35c46a; }
.rm-agent[data-tone="warn"] i { background: #f5c518; box-shadow: 0 0 10px #f5c518; animation: rm-blink 1.2s infinite; }
.rm-agent[data-tone="bad"] i { background: #ff4d4f; box-shadow: 0 0 10px #ff4d4f; }
@keyframes rm-blink { 50% { opacity: 0.35; } }
.rm-approvals { list-style: none; margin: 0; padding: 0; }
.rm-approvals li { display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; margin-bottom: 8px; border-radius: 10px; background: rgba(245, 197, 24, 0.08); border: 1px solid rgba(245, 197, 24, 0.35); }
.rm-approvals li[data-esc="1"] { background: rgba(255, 77, 79, 0.1); border-color: rgba(255, 77, 79, 0.5); }
.rm-approvals b { color: #f5c518; font-size: 13px; }
.rm-approvals span { color: #b9cbe0; font-size: 12px; }
.rm-approvals em { font-style: normal; color: #ff8f8f; font-size: 11px; }

#rescue-map-legend { position: absolute; top: 64px; right: 372px; width: min(300px, calc(100vw - 24px)); padding: 10px 12px; max-height: calc(100vh - 100px); overflow: auto; ${CARD} z-index: 5; }
#rescue-map-legend[hidden] { display: none; }
#rescue-map-legend h4 { margin: 0 0 6px; font: 600 12px system-ui, sans-serif; color: #7fb2e6; letter-spacing: 0.14em; }
html.rm-hide-right #rescue-map-legend { right: 16px; }

.rm-label { position: absolute; left: 0; top: 0; white-space: nowrap; font: 11px/1 system-ui, sans-serif; transform-origin: 0 0; margin: -6px 0 0 4px; }
.rm-label[data-kind="flow"], .rm-label[data-kind="section"], .rm-label[data-kind="note"] { font-size: 10px; opacity: 0.85; }
.rm-label.rm-hover { color: #4fd1ff !important; text-decoration: underline; text-underline-offset: 2px; }
.rm-label.rm-picked { animation: rm-picked 0.9s ease-out; }
@keyframes rm-picked { 0% { background: rgba(79, 209, 255, 0.55); box-shadow: 0 0 0 4px rgba(79, 209, 255, 0.55); } 100% { background: transparent; box-shadow: 0 0 0 4px transparent; } }
#rescue-map-toast { position: fixed; left: 50%; bottom: calc(min(40vh, 460px) + 28px); transform: translateX(-50%); z-index: 12; padding: 8px 14px; border-radius: 10px; font-size: 13px; color: #eaf4ff; pointer-events: none; ${CARD} }
#rescue-map-toast[hidden] { display: none; }
[data-theme="dark"] .rm-label { color: #d6e2ee; text-shadow: 0 0 3px #0b1522, 0 0 3px #0b1522; }
[data-theme="dark"] .rm-label[data-kind="flow"] { color: #7ad9a1; }
.rm-evac-total b { font-size: 22px; color: #ffb020; }
.rm-evac-total small { margin-left: 6px; opacity: .7; }
.rm-evac-group { padding: 6px 0; border-top: 1px solid rgba(255,255,255,.08); font-size: 12px; }
.rm-evac-group[data-anchor] { cursor: pointer; }
.rm-evac-group[data-blocked="1"] b { color: #ff4d4f; }
.rm-evac-route { opacity: .9; margin-top: 2px; }
.rm-evac-route[data-tone="bad"] { color: #ff8a8c; }
.rm-fig { position: absolute; left: 0; top: 0; pointer-events: none; font-size: 11px; line-height: 1.3; padding: 1px 6px; border-radius: 9px; white-space: nowrap; color: #fff; background: rgba(20,24,32,.82); border: 1px solid rgba(255,255,255,.25); margin: -10px 0 0 -4px; }
.rm-fig-group { background: #b8860b; font-weight: 600; font-size: 12px; }
.rm-fig-blocked { background: #d83a3d; }
.rm-fig-route { background: #1f9d55; }
.rm-fig-contaminated { background: rgba(216,58,61,.85); }
.rm-fig-target { background: #1f6f9d; }
.rm-fig-stale { opacity: .55; filter: grayscale(1); }
.rm-mark {
  position: absolute; left: 0; top: 0; white-space: nowrap; font: 600 12px/1 system-ui, sans-serif; padding: 4px 8px;
  border-radius: 6px; color: #fff; margin: -30px 0 0 -4px; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.45);
}
.rm-mark[data-level="info"] { background: #2f7fd1; }
.rm-mark[data-level="warn"] { background: #c98300; }
.rm-mark[data-level="alarm"] { background: #d83a3d; }
.rm-mark[data-level="ok"] { background: #1f9d55; }
.rm-mark[data-level="blue"] { background: #2f7fd1; }
.rm-mark[data-level="yellow"] { background: #b8860b; }
.rm-mark[data-level="orange"] { background: #d9680a; }
.rm-mark[data-level="red"] { background: #d83a3d; }

@media (max-width: 1100px) {
  ${ON} { --rm-l: 312px; --rm-r: 312px; }
  .rm-left, .rm-right { width: 280px; }
  .rm-chips { display: none; }
  #rescue-map-legend { right: 312px; }
}
@media (max-width: 800px) {
  ${ON} { --rm-l: 12px; --rm-r: 12px; }
  .rm-left, .rm-right { display: none; }
  ${ON} ${CENTER} { left: 12px; right: 12px; bottom: 12px; height: min(60vh, 560px); }
  .rm-clock, .rm-title span { display: none; }
  .rm-tools { overflow-x: auto; max-width: 60vw; }
}
`

export interface HudHandlers {
  legend(): Legend | null
  title(): string
  /** 面板显示/隐藏之后调用，让地图重新适配 */
  layoutChanged(): void
  focus(name: string): void
  tilt(): number
  setTilt(deg: number): void
  reset(): void
}

export interface Hud {
  /** 在聊天区上方显示一条短提示，几秒后消失 */
  toast(text: string): void
  /** 聊天区被隐藏时把它显示出来（点选引用要往输入框里写） */
  showChat(): void
  setConnected(ok: boolean): void
  setState(s: StateData | null, staleS: number, failed: boolean): void
  dispose(): void
}

const root = document.documentElement
const PANEL_CLASSES = ['rm-hide-left', 'rm-hide-chat', 'rm-hide-right']

function readFlag(key: string): boolean {
  try {
    return localStorage.getItem(`rescue-map:${key}`) === '1'
  } catch {
    return false
  }
}
function writeFlag(key: string, v: boolean): void {
  try {
    localStorage.setItem(`rescue-map:${key}`, v ? '1' : '0')
  } catch {
    /* 隐私模式等：不影响使用 */
  }
}

/** 强制 DSH 用深色变量：body 上的 data-ds-dark-theme 被 DSH 的主题逻辑拿掉时再加回去。返回恢复函数。 */
function forceDark(): () => void {
  const body = document.body
  const had = body.hasAttribute('data-ds-dark-theme')
  const ensure = (): void => {
    if (!body.hasAttribute('data-ds-dark-theme')) body.setAttribute('data-ds-dark-theme', '')
  }
  ensure()
  const mo = new MutationObserver(ensure)
  mo.observe(body, { attributes: true, attributeFilter: ['data-ds-dark-theme'] })
  return () => {
    mo.disconnect()
    if (!had) body.removeAttribute('data-ds-dark-theme')
  }
}

export function mountHud(hd: HudHandlers): Hud {
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = CSS
  document.head.appendChild(style)
  root.classList.add('rm-on')
  const restoreTheme = forceDark()

  const hud = document.createElement('div')
  hud.id = HUD_ID
  // 放在 #root 前面：DSH 的抽屉和弹窗（#root 里）要盖在自绘面板上面
  document.body.insertBefore(hud, document.getElementById('root'))
  const panels: PanelsHandle = mountPanels(hud, hd.title(), (name) => hd.focus(name))
  const tools = hud.querySelector('.rm-tools') as HTMLElement

  const btn = (text: string, onClick: () => void, pressed?: boolean): HTMLButtonElement => {
    const b = h('button', { type: 'button' }, text)
    b.addEventListener('click', onClick)
    if (pressed !== undefined) b.setAttribute('aria-pressed', String(pressed))
    tools.appendChild(b)
    return b
  }

  const toggles: { b: HTMLButtonElement; cls: string }[] = []
  for (const [key, cls, label] of [['left', 'rm-hide-left', '左栏'], ['chat', 'rm-hide-chat', '对话'], ['right', 'rm-hide-right', '右栏']] as const) {
    const hidden = readFlag(`hide-${key}`)
    root.classList.toggle(cls, hidden)
    const b = btn(label, () => {
      const nowHidden = !root.classList.contains(cls)
      root.classList.toggle(cls, nowHidden)
      writeFlag(`hide-${key}`, nowHidden)
      b.setAttribute('aria-pressed', String(!nowHidden))
      requestAnimationFrame(() => hd.layoutChanged())
    }, !hidden)
    toggles.push({ b, cls })
  }
  btn('会话', () => root.classList.toggle('rm-drawer'))
  btn('全屏地图', () => {
    const anyShown = toggles.some((t) => !root.classList.contains(t.cls))
    toggles.forEach((t, i) => {
      root.classList.toggle(t.cls, anyShown)
      writeFlag(`hide-${['left', 'chat', 'right'][i]}`, anyShown)
      t.b.setAttribute('aria-pressed', String(!anyShown))
    })
    requestAnimationFrame(() => hd.layoutChanged())
  })

  const legendData = hd.legend()
  const legendBox = document.createElement('div')
  legendBox.id = 'rescue-map-legend'
  legendBox.hidden = true
  if (legendData) {
    const lb = btn('图例', () => {
      legendBox.hidden = !legendBox.hidden
      if (!legendBox.hidden) legendBox.replaceChildren(h('h4', null, '图例'), renderLegend(legendData, true))
      lb.setAttribute('aria-pressed', String(!legendBox.hidden))
    }, false)
    hud.appendChild(legendBox)
  }
  btn('倾斜视角', () => hd.setTilt(hd.tilt() > 1 ? 0 : 40))
  btn('复位', () => hd.reset())

  const toastEl = document.createElement('div')
  toastEl.id = 'rescue-map-toast'
  toastEl.hidden = true
  hud.appendChild(toastEl)
  let toastTimer = 0
  const chatToggle = toggles.find((t) => t.cls === 'rm-hide-chat')

  let connected = false
  return {
    toast(text) {
      toastEl.textContent = text
      toastEl.hidden = false
      window.clearTimeout(toastTimer)
      toastTimer = window.setTimeout(() => (toastEl.hidden = true), 3000)
    },
    showChat() {
      if (!root.classList.contains('rm-hide-chat')) return
      root.classList.remove('rm-hide-chat')
      writeFlag('hide-chat', false)
      chatToggle?.b.setAttribute('aria-pressed', 'true')
      requestAnimationFrame(() => hd.layoutChanged())
    },
    setConnected(ok) {
      connected = ok
      hud.dataset.channel = String(connected)
    },
    setState: (s, staleS, failed) => panels.update(s, staleS, failed),
    dispose() {
      panels.dispose()
      hud.remove()
      style.remove()
      restoreTheme()
      root.classList.remove('rm-on', 'rm-drawer', ...PANEL_CLASSES)
    },
  }
}

/** 地图加载失败时退回 DSH 原界面。 */
export function restoreNative(): void {
  document.getElementById(STYLE_ID)?.remove()
  document.getElementById(HUD_ID)?.remove()
  root.classList.remove('rm-on', 'rm-drawer')
}

/** 悬浮面板当前盖住的边（像素）：左栏、底部对话区、右栏；顶部给顶栏留位置。隐藏的面板不算。 */
export function panelInsets(): Insets {
  const w = window.innerWidth
  const hgt = window.innerHeight
  const rect = (sel: string): DOMRect | null => {
    const e = document.querySelector<HTMLElement>(sel)
    if (!e || getComputedStyle(e).display === 'none') return null
    const r = e.getBoundingClientRect()
    return r.width > 0 && r.height > 0 ? r : null
  }
  const left = rect('.rm-left')
  const right = rect('.rm-right')
  const chat = rect(`${FRAME} > [class$="_centerCol"]`)
  return {
    left: left ? left.right : 0,
    right: right ? w - right.left : 0,
    top: 60,
    bottom: chat ? hgt - chat.top : 0,
  }
}
