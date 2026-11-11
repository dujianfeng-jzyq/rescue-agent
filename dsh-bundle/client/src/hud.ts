/**
 * 大屏界面（docs/design-v4-map.md 第 6 节）：地图在最底层，上面是
 *   顶栏（标题、时间、状态、工具）、左栏（矿井信息）、右栏顶部的智能体卡片——这几块是自绘的（panels.ts）；
 *   右栏其余部分是 DSH 的会话组件（消息、输入框、人工确认、提问卡片原样保留），只换皮肤、改位置，
 *   并隐藏演示用不上的控件（视图标签、权限模式、模型选择、用量统计等）。地图占中间整块高度。
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
${ON} { --rm-l: 372px; --rm-chat-w: clamp(420px, 28vw, 560px); --rm-r: calc(var(--rm-chat-w) + 32px); --rm-top: 68px; --rm-agent-h: 0px; }
${ON}.rm-hide-left { --rm-l: 16px; }
${ON}.rm-hide-right.rm-hide-chat { --rm-r: 16px; }
${ON} #root { position: relative; z-index: 2; height: 100%; pointer-events: none; }
${ON} ${WRAP} { height: 100%; background: transparent !important; pointer-events: none; }
${ON} ${FRAME} { display: block !important; position: relative; height: 100%; background: transparent !important; pointer-events: none; }
${ON} ${RIGHTCOL} { display: contents !important; }
${ON} ${OVERLAY} { pointer-events: none; }
${ON} ${OVERLAY} > * { pointer-events: auto; }

/* 右栏：上面是自绘的智能体卡片（高度由 JS 写进 --rm-agent-h），下面是 DSH 的会话组件，占满剩下的高度 */
${ON} ${CENTER} { position: absolute !important; right: 16px; width: var(--rm-chat-w); top: calc(var(--rm-top) + var(--rm-agent-h) + 12px); bottom: 16px; pointer-events: auto; ${NAVY} ${CARD} }
${ON}.rm-hide-right ${CENTER} { top: var(--rm-top); }
${ON} ${CENTER} > * { background: transparent !important; }
${ON}.rm-hide-chat ${CENTER} { display: none !important; }
/* 人工确认卡片和提问卡片（处置选项、现场条件）放到画面正中（2026-11-10 演示复看：右下角不醒目，窄栏里显示不全）。
   卡片仍是 DSH 会话组件里的原件，只改位置：会话列的 backdrop-filter 会让 fixed 定位相对它而不是视口，有卡片时去掉；
   对话区被收起时也要显示卡片（列保留布局但不可见，只有卡片可见）。
   外层（DSH 的 root/frame）只当定位容器：不加底色、边框和压暗（第八次复看：外面多一层背景），里面的卡片拉满宽度 */
${ON} ${CENTER}:has([data-approval-key], [data-question-key]) { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; background: rgba(9, 20, 38, 0.95) !important; }
${ON}.rm-hide-chat ${CENTER}:has([data-approval-key], [data-question-key]) { display: block !important; visibility: hidden; }
${ON} ${CENTER} :is([data-approval-key], [data-question-key]) {
  visibility: visible; position: fixed !important; left: 50vw; top: 50vh; transform: translate(-50%, -50%); z-index: 100;
  width: min(820px, calc(100vw - 32px)) !important; max-width: none !important; box-sizing: border-box; margin: 0 !important; padding: 0 !important;
  background: transparent !important; border: 0 !important; box-shadow: none !important;
}
${ON} ${CENTER} :is([data-approval-key], [data-question-key]) > * {
  width: 100% !important; max-width: none !important; max-height: 82vh !important; overflow: auto; box-sizing: border-box; margin: 0 !important;
  background: rgb(12, 28, 52) !important; box-shadow: 0 16px 60px rgba(0, 0, 0, 0.65) !important;
}
${ON} ${CENTER} [data-approval-key] > * { border: 1px solid rgba(245, 197, 24, 0.7) !important; }
${ON} ${CENTER} [data-question-key] > * { border: 1px solid rgba(90, 160, 255, 0.6) !important; }
${ON} ${CENTER} [data-approval-key] [role="group"] > div:first-child { font-size: 15px; line-height: 24px; }
${ON} ${CENTER} [data-approval-key] [role="group"] > div:first-child::first-line { font-size: 18px; }
/* 会话组件里演示用不上的控件：视图标签（Chat/Trajectory）、内容宽度拖柄、轮次导航、权限模式、模型选择、附件按钮、用量统计 */
${ON} ${CENTER} header [class$="_tabs"],
${ON} ${CENTER} [data-width-handle], ${ON} ${CENTER} nav[aria-label="Turn navigation"],
${ON} ${CENTER} [data-composer-card] [class$="_tools"], ${ON} ${CENTER} [data-composer-card] [class$="_standardControls"],
${ON} ${CENTER} [data-composer-dock] { display: none !important; }
${ON} ${CENTER} header { height: auto !important; min-height: 0 !important; padding-top: 8px !important; padding-bottom: 8px !important; border-bottom: 1px solid rgba(120, 160, 210, 0.16); }
/* 消息列：占满右栏宽度（DSH 默认限制在约 750px 并居中） */
${ON} ${CENTER} [data-chat-flow] { width: auto !important; max-width: none !important; margin-left: 0 !important; margin-right: 0 !important; padding-left: 14px !important; padding-right: 14px !important; }
/* 输入框：一行（输入 + 发送），不透明底，消息从它下面滚过去时不再透出来 */
${ON} ${CENTER} [data-composer-seat] { background: linear-gradient(180deg, rgba(9, 20, 38, 0) 0, rgba(9, 20, 38, 0.97) 20px) !important; padding: 18px 12px 12px !important; box-sizing: border-box; }
/* 卡片本身的布局不动（人工确认、提问卡片挂在它的 overlay 锚点上），只拉满宽度；工具都隐藏后那一行只剩发送按钮，挪到输入框右侧 */
${ON} ${CENTER} [data-composer-card] { align-self: stretch !important; width: auto !important; max-width: none !important; margin: 0 !important; background: rgba(12, 28, 52, 0.98) !important; border: 1px solid rgba(90, 160, 255, 0.3) !important; }
${ON} ${CENTER} [data-composer-card] [data-input-scroll] { padding-right: 44px; box-sizing: border-box; }
${ON} ${CENTER} [data-composer-card] > [class$="_row"] { position: absolute !important; left: 0; right: 6px; top: 0; bottom: 0; width: auto !important; min-height: 0 !important; padding: 0 !important; display: flex !important; justify-content: flex-end; align-items: center !important; pointer-events: none; }
${ON} ${CENTER} [data-composer-card] > [class$="_row"] > * { flex: 0 0 auto !important; pointer-events: auto; }

/* DSH 的会话列表：按钮呼出的抽屉 */
${ON} ${SIDEBAR} { display: none !important; }
${ON}.rm-drawer ${SIDEBAR} { display: block !important; position: absolute !important; left: 16px; top: var(--rm-top); bottom: 16px; width: 320px; z-index: 30; pointer-events: auto; ${NAVY} ${CARD} }
${ON}.rm-drawer ${SIDEBAR} > * { background: transparent !important; }

/* DSH 的右栏（文件预览、终端等）：打开时浮在最上面，收起时不占位 */
${ON} ${FRAME}[data-rightbar-collapsed] [data-sidebar-right-panel] { display: none !important; }
/* 放在右栏左边，不盖住聊天区 */
${ON} ${RIGHT} { position: absolute !important; right: var(--rm-r); top: var(--rm-top); bottom: 16px; width: min(560px, calc(100vw - var(--rm-r) - 16px)) !important; z-index: 40; pointer-events: auto; ${NAVY} ${CARD} }

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
.rm-left, .rm-right { box-sizing: border-box; position: absolute; top: var(--rm-top); overflow-y: auto; scrollbar-width: thin; scrollbar-color: rgba(120, 160, 210, 0.35) transparent; }
.rm-left { left: 16px; bottom: 16px; width: 340px; padding: 6px 14px 14px; }
/* 智能体卡片：在聊天区上方，按内容高度，最多占三分之一屏；聊天区隐藏时可以往下长 */
.rm-right { right: 16px; width: var(--rm-chat-w); max-height: 34vh; padding: 2px 14px 8px; }
html.rm-hide-chat .rm-right { max-height: calc(100vh - var(--rm-top) - 16px); }
html.rm-hide-left .rm-left, html.rm-hide-right .rm-right { display: none; }
.rm-sec { padding: 10px 0 4px; border-bottom: 1px solid rgba(120, 160, 210, 0.12); }
.rm-sec:last-child { border-bottom: 0; }
.rm-sec h3 { margin: 0 0 8px; font-size: 12px; font-weight: 600; letter-spacing: 0.16em; color: #7fb2e6; display: flex; align-items: center; gap: 8px; }
.rm-sec h3::before { content: ''; width: 3px; height: 12px; border-radius: 2px; background: #4fd1ff; box-shadow: 0 0 8px #4fd1ff; }
.rm-empty { color: #7f93ab; padding: 6px 0 8px; font-size: 12px; }
.rm-note { margin: 6px 0 4px; color: #7f93ab; font-size: 11px; }
.rm-sealed-note { margin: 0 0 6px; color: #d9b84a; font-size: 12px; }
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
.rm-demo-end { display: flex; flex-direction: column; gap: 2px; margin: 0 0 8px; padding: 8px 10px; border: 1px solid rgba(122, 217, 161, 0.5); border-radius: 6px; background: rgba(122, 217, 161, 0.1); }
.rm-demo-end b { color: #7ad9a1; font-size: 13px; }
.rm-demo-end span { color: #b9cbe0; font-size: 12px; }
.rm-val[data-q="sealed_off"] b, .rm-val[data-q="sealed_off"] small { color: #7f93ab; }

.rm-timeline { list-style: none; margin: 0; padding: 0 0 0 12px; border-left: 1px solid rgba(120, 160, 210, 0.25); }
.rm-timeline li { position: relative; padding: 0 0 10px 8px; display: grid; grid-template-columns: auto 1fr; column-gap: 8px; }
.rm-timeline li::before { content: ''; position: absolute; left: -16px; top: 5px; width: 7px; height: 7px; border-radius: 50%; background: var(--c); box-shadow: 0 0 8px var(--c); }
.rm-timeline time, .rm-list time { color: #7f93ab; font: 11px ui-monospace, Menlo, Consolas, monospace; grid-row: 1; }
.rm-timeline b { color: var(--c); font-weight: 600; font-size: 12px; }
.rm-timeline span { grid-column: 1 / -1; color: #b9cbe0; font-size: 12px; overflow-wrap: anywhere; }
.rm-list { list-style: none; margin: 0; padding: 0; }
.rm-list li { display: flex; align-items: center; gap: 8px; padding: 5px 0; border-bottom: 1px solid rgba(120, 160, 210, 0.1); font-size: 12px; }
.rm-list li em { margin-left: auto; font-style: normal; color: #ff8a1f; font-size: 11px; }
.rm-records code { flex: 1 1 auto; min-width: 0; font: 11px ui-monospace, Menlo, Consolas, monospace; color: #9fc4ee; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rm-records time { margin-left: auto; flex: 0 0 auto; white-space: nowrap; }
.rm-records li { cursor: pointer; }
.rm-records li:hover, .rm-records li:focus-visible { background: rgba(79, 209, 255, 0.08); outline: none; }
.rm-modal-overlay { position: fixed; inset: 0; z-index: 40; display: flex; align-items: center; justify-content: center; background: rgba(3, 8, 18, 0.6); padding: 16px; box-sizing: border-box; }
.rm-modal { width: min(720px, 100%); max-height: min(80vh, 100%); display: flex; flex-direction: column; background: rgba(12, 28, 52, 0.98); border: 1px solid rgba(90, 160, 255, 0.35); border-radius: 10px; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5); color: #dbe7f5; font-size: 13px; }
.rm-modal-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px 6px; }
.rm-modal-head b { font-size: 15px; color: #eaf4ff; }
.rm-modal-close { border: 0; background: transparent; color: #9fb3cc; font-size: 22px; line-height: 1; cursor: pointer; padding: 0 4px; }
.rm-modal-close:hover { color: #eaf4ff; }
.rm-modal-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; padding: 0 16px 10px; border-bottom: 1px solid rgba(120, 160, 210, 0.15); color: #7f93ab; font-size: 12px; }
.rm-modal-meta code { font: 11px ui-monospace, Menlo, Consolas, monospace; color: #9fc4ee; }
.rm-modal-link { border: 0; background: transparent; padding: 0; cursor: pointer; font: 11px ui-monospace, Menlo, Consolas, monospace; color: #4fd1ff; text-decoration: underline; }
.rm-modal-body { padding: 12px 16px 16px; overflow: auto; }
.rm-md h4 { margin: 4px 0 8px; font-size: 14px; color: #eaf4ff; }
.rm-md p { margin: 6px 0; line-height: 1.6; }
.rm-md ul { margin: 6px 0; padding-left: 18px; }
.rm-md li { margin: 3px 0; line-height: 1.6; }
.rm-modal-kv { grid-template-columns: max-content 1fr; }
.rm-modal-kv dd { word-break: break-all; }
.rm-kind { padding: 1px 6px; border-radius: 4px; background: rgba(79, 209, 255, 0.14); color: #4fd1ff; font-size: 11px; white-space: nowrap; }

.rm-agent { display: flex; align-items: center; gap: 10px; font-size: 16px; padding: 2px 0 6px; }
.rm-agent small { color: #7f93ab; font-size: 11px; font-weight: 400; }
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

#rescue-map-legend { position: absolute; top: 64px; right: var(--rm-r); width: min(300px, calc(100vw - 24px)); padding: 10px 12px; max-height: calc(100vh - 100px); overflow: auto; ${CARD} z-index: 5; }
#rescue-map-legend[hidden] { display: none; }
#rescue-map-legend h4 { margin: 0 0 6px; font: 600 12px system-ui, sans-serif; color: #7fb2e6; letter-spacing: 0.14em; }

.rm-label { position: absolute; left: 0; top: 0; white-space: nowrap; font: 11px/1 system-ui, sans-serif; transform-origin: 0 0; margin: -6px 0 0 4px; }
.rm-label[data-kind="flow"], .rm-label[data-kind="section"], .rm-label[data-kind="note"] { font-size: 10px; opacity: 0.85; }
.rm-label.rm-hover { color: #4fd1ff !important; text-decoration: underline; text-underline-offset: 2px; }
.rm-label.rm-picked { animation: rm-picked 0.9s ease-out; }
@keyframes rm-picked { 0% { background: rgba(79, 209, 255, 0.55); box-shadow: 0 0 0 4px rgba(79, 209, 255, 0.55); } 100% { background: transparent; box-shadow: 0 0 0 4px transparent; } }
#rescue-map-toast { position: fixed; left: calc((var(--rm-l) + 100vw - var(--rm-r)) / 2); bottom: 28px; transform: translateX(-50%); z-index: 12; padding: 8px 14px; border-radius: 10px; font-size: 13px; color: #eaf4ff; pointer-events: none; ${CARD} }
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

/* 矮屏（投影仪 768 高等）：智能体卡片只留状态和待确认指令，把高度让给聊天区 */
@media (max-height: 900px) {
  .rm-sec-records { display: none; }
}
@media (max-width: 1100px) {
  ${ON} { --rm-l: 312px; --rm-chat-w: 360px; }
  .rm-left { width: 280px; }
  .rm-chips { display: none; }
}
@media (max-width: 800px) {
  ${ON} { --rm-l: 12px; --rm-r: 12px; }
  .rm-left, .rm-right { display: none; }
  ${ON} ${CENTER} { left: 12px; right: 12px; width: auto; top: auto; bottom: 12px; height: min(60vh, 560px); }
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
  // 智能体卡片的高度决定聊天区从哪里开始（CSS 变量 --rm-agent-h）
  const agentCard = hud.querySelector('.rm-right') as HTMLElement
  const ro = new ResizeObserver(() => {
    const hgt = agentCard.offsetHeight
    root.style.setProperty('--rm-agent-h', `${hgt}px`)
    requestAnimationFrame(() => hd.layoutChanged())
  })
  ro.observe(agentCard)

  const btn = (text: string, onClick: () => void, pressed?: boolean): HTMLButtonElement => {
    const b = h('button', { type: 'button' }, text)
    b.addEventListener('click', onClick)
    if (pressed !== undefined) b.setAttribute('aria-pressed', String(pressed))
    tools.appendChild(b)
    return b
  }

  const toggles: { b: HTMLButtonElement; cls: string }[] = []
  for (const [key, cls, label] of [['left', 'rm-hide-left', '左栏'], ['right', 'rm-hide-right', '智能体'], ['chat', 'rm-hide-chat', '对话']] as const) {
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
      writeFlag(`hide-${['left', 'right', 'chat'][i]}`, anyShown)
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
      ro.disconnect()
      root.style.removeProperty('--rm-agent-h')
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

/** 悬浮面板当前盖住的边（像素）：左栏、右栏（智能体卡片 + 对话区；窄屏时对话区在底部）；顶部给顶栏留位置。隐藏的面板不算。 */
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
  const chatAtBottom = !!chat && chat.left < w / 2
  const rightEdge = Math.min(right ? right.left : w, chat && !chatAtBottom ? chat.left : w)
  return {
    left: left ? left.right : 0,
    right: w - rightEdge,
    top: 60,
    bottom: chat && chatAtBottom ? hgt - chat.top : 0,
  }
}
