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
/**
 * 面板滑动时长（毫秒）。缓动曲线前后对称：原来 0.38 秒、前快后慢，帧率低时大部分位移落在头两三帧里，看起来像跳过去。
 * 新告警时镜头等面板滑完再飞（index.ts）。
 */
export const SLIDE_MS = 550
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
${ON} body { overflow: hidden; }
#rescue-map-host { position: fixed; inset: 0; z-index: 0; }
${ON} { --rm-l: 372px; --rm-chat-w: clamp(420px, 28vw, 560px); --rm-r: calc(var(--rm-chat-w) + 32px); --rm-top: 68px; --rm-agent-h: 0px; --rm-slide-t: ${SLIDE_MS / 1000}s; --rm-ease: cubic-bezier(0.45, 0, 0.25, 1); }
/* 滑动期间去掉毛玻璃：移动中的面板每帧都要重新模糊背后的地图，配置弱的机器上掉帧，滑动看起来像跳过去（2026-11-18 演示） */
${ON}.rm-sliding .rm-card, ${ON}.rm-sliding ${CENTER}, ${ON}.rm-sliding ${SIDEBAR}, ${ON}.rm-sliding #rescue-map-legend { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }
${ON}.rm-sliding .rm-left, ${ON}.rm-sliding .rm-right, ${ON}.rm-sliding ${SIDEBAR} { will-change: transform; }
${ON}.rm-hide-left { --rm-l: 16px; }
${ON}.rm-hide-right.rm-hide-chat { --rm-r: 16px; }
${ON} #root { position: relative; z-index: 2; height: 100%; pointer-events: none; }
${ON} ${WRAP} { height: 100%; background: transparent !important; pointer-events: none; }
${ON} ${FRAME} { display: block !important; position: relative; height: 100%; background: transparent !important; pointer-events: none; }
${ON} ${RIGHTCOL} { display: contents !important; }
${ON} ${OVERLAY} { pointer-events: none; }
${ON} ${OVERLAY} > * { pointer-events: auto; }

/* 右栏：上面是自绘的智能体卡片（高度由 JS 写进 --rm-agent-h），下面是 DSH 的会话组件，占满剩下的高度 */
${ON} ${CENTER} { position: absolute !important; right: 16px; width: var(--rm-chat-w); top: calc(var(--rm-top) + var(--rm-agent-h) + 12px); bottom: 16px; pointer-events: auto; ${NAVY} ${CARD}
  transition: right var(--rm-slide-t) var(--rm-ease), bottom var(--rm-slide-t) var(--rm-ease), top var(--rm-slide-t) var(--rm-ease), visibility 0s; }
${ON}.rm-hide-right ${CENTER} { top: var(--rm-top); }
${ON} ${CENTER} > * { background: transparent !important; }
/* 面板收起：滑出屏幕，滑完再隐藏（visibility 延迟到滑动结束）；展开时立即可见再滑进来。
   会话列滑动用 right 而不是 transform：有 transform 时里面 fixed 定位的确认卡片会跟着它走 */
${ON}.rm-hide-chat ${CENTER} { right: calc(-1 * var(--rm-chat-w) - 24px); visibility: hidden; pointer-events: none;
  transition: right var(--rm-slide-t) var(--rm-ease), bottom var(--rm-slide-t) var(--rm-ease), top var(--rm-slide-t) var(--rm-ease), visibility 0s linear var(--rm-slide-t); }
/* 人工确认卡片和提问卡片（处置选项、现场条件）放到画面下方、左右栏之间的区域，底边对齐面板（2026-11-10 演示复看：右下角不醒目，
   窄栏里显示不全，先移到正中；2026-11-17：正中会挡住地图，改到下方）。宽度不超过左右栏之间的空隙，高度最多半屏，超出在卡片里滚动。
   卡片仍是 DSH 会话组件里的原件，只改位置：会话列的 backdrop-filter 会让 fixed 定位相对它而不是视口，有卡片时去掉；
   对话区被收起时也要显示卡片（列保留布局但不可见，只有卡片可见）。
   外层（DSH 的 root/frame）只当定位容器：不加底色、边框和压暗（第八次复看：外面多一层背景），里面的卡片拉满宽度 */
${ON} ${CENTER}:has([data-approval-key], [data-question-key]) { backdrop-filter: none !important; -webkit-backdrop-filter: none !important; background: rgba(9, 20, 38, 0.95) !important; }
${ON} ${CENTER} :is([data-approval-key], [data-question-key]) {
  visibility: visible; pointer-events: auto; position: fixed !important; left: calc((var(--rm-l) + 100vw - var(--rm-r)) / 2); top: auto; bottom: 16px; transform: translateX(-50%); z-index: 100;
  width: min(820px, calc(100vw - var(--rm-l) - var(--rm-r))) !important; max-width: none !important; box-sizing: border-box; margin: 0 !important; padding: 0 !important;
  background: transparent !important; border: 0 !important; box-shadow: none !important;
}
${ON} ${CENTER} :is([data-approval-key], [data-question-key]) > * {
  width: 100% !important; max-width: none !important; max-height: 50vh !important; overflow: auto; box-sizing: border-box; margin: 0 !important;
  background: rgb(12, 28, 52) !important; box-shadow: 0 16px 60px rgba(0, 0, 0, 0.65) !important;
  animation: rm-card-in var(--rm-slide-t) var(--rm-ease);
}
/* 卡片被 DSH 移除后留一个克隆往下滑出（hud.ts watchCards），样式照上面的卡片 */
.rm-ghost { position: fixed; left: calc((var(--rm-l) + 100vw - var(--rm-r)) / 2); bottom: 16px; transform: translateX(-50%); z-index: 100; pointer-events: none; ${NAVY} }
.rm-ghost > * { margin: 0 !important; }
.rm-ghost > * > * { width: 100% !important; max-width: none !important; max-height: 50vh !important; overflow: hidden; box-sizing: border-box; margin: 0 !important;
  background: rgb(12, 28, 52) !important; box-shadow: 0 16px 60px rgba(0, 0, 0, 0.65) !important; animation: rm-card-out 0.3s ease-in forwards; }
.rm-ghost [data-approval-key] > * { border: 1px solid rgba(245, 197, 24, 0.7) !important; }
.rm-ghost [data-question-key] > * { border: 1px solid rgba(90, 160, 255, 0.6) !important; }
@keyframes rm-card-in { from { transform: translateY(calc(100% + 24px)); opacity: 0; } to { transform: none; opacity: 1; } }
@keyframes rm-card-out { from { transform: none; opacity: 1; } to { transform: translateY(calc(100% + 24px)); opacity: 0; } }
@keyframes rm-from-left { from { transform: translateX(calc(-100% - 24px)); } to { transform: none; } }
@keyframes rm-from-right { from { transform: translateX(calc(100% + 24px)); opacity: 0.4; } to { transform: none; opacity: 1; } }
@keyframes rm-fade-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes rm-fade-out { from { opacity: 1; } to { opacity: 0; } }
@keyframes rm-pop-in { from { transform: translateY(40px); opacity: 0; } to { transform: none; opacity: 1; } }
@keyframes rm-pop-out { from { transform: none; opacity: 1; } to { transform: translateY(40px); opacity: 0; } }
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
${ON} ${SIDEBAR} { display: block !important; position: absolute !important; left: 16px; top: var(--rm-top); bottom: 16px; width: 320px; z-index: 30; pointer-events: none; ${NAVY} ${CARD}
  transform: translateX(calc(-100% - 24px)); visibility: hidden; transition: transform var(--rm-slide-t) var(--rm-ease), visibility 0s linear var(--rm-slide-t); }
${ON}.rm-drawer ${SIDEBAR} { transform: none; visibility: visible; pointer-events: auto; transition: transform var(--rm-slide-t) var(--rm-ease), visibility 0s; }
${ON}.rm-drawer ${SIDEBAR} > * { background: transparent !important; }

/* DSH 的右栏（文件预览、终端等）：打开时浮在最上面，收起时不占位 */
${ON} ${FRAME}[data-rightbar-collapsed] [data-sidebar-right-panel] { display: none !important; }
/* 放在右栏左边，不盖住聊天区 */
${ON} ${RIGHT} { position: absolute !important; right: var(--rm-r); top: var(--rm-top); bottom: 16px; width: min(560px, calc(100vw - var(--rm-r) - 16px)) !important; z-index: 40; pointer-events: auto; ${NAVY} ${CARD}
  animation: rm-from-right var(--rm-slide-t) var(--rm-ease); }

/* 自绘部分 */
#${HUD_ID} { position: fixed; inset: 0; z-index: 1; pointer-events: none; color: #dbe8f5; font: 13px/1.45 system-ui, -apple-system, 'Segoe UI', 'Noto Sans CJK SC', 'PingFang SC', sans-serif; }
#${HUD_ID} > * { pointer-events: auto; }
.rm-header {
  position: absolute; top: 0; left: 0; right: 0; height: 56px; display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: center; gap: 16px; padding: 0 16px;
  background: linear-gradient(180deg, rgba(8, 18, 34, 0.92), rgba(8, 18, 34, 0.55)); border-bottom: 1px solid rgba(90, 160, 255, 0.22);
  box-shadow: 0 6px 30px rgba(0, 0, 0, 0.35); backdrop-filter: blur(10px);
}
.rm-header-left { display: flex; align-items: center; gap: 14px; min-width: 0; justify-self: start; }
.rm-brand { display: flex; align-items: center; gap: 10px; min-width: 0; justify-self: center; }
.rm-logo { display: grid; place-items: center; filter: drop-shadow(0 0 6px rgba(79, 209, 255, 0.55)); }
.rm-title { display: flex; flex-direction: column; min-width: 0; }
.rm-title b { font-size: 17px; letter-spacing: 0.12em; color: #eaf4ff; text-shadow: 0 0 12px rgba(79, 209, 255, 0.45); white-space: nowrap; }
.rm-title span { font-size: 11px; color: #7fb2e6; letter-spacing: 0.06em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rm-clock { text-align: left; flex: 0 0 auto; }
.rm-clock-main { font: 600 18px/1.1 ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace; color: #4fd1ff; text-shadow: 0 0 10px rgba(79, 209, 255, 0.5); white-space: nowrap; }
.rm-clock-sub { font-size: 11px; color: #7f93ab; margin-top: 2px; }
.rm-header-right { display: flex; align-items: center; gap: 12px; min-width: 0; justify-self: end; }
.rm-chips { display: flex; gap: 6px; min-width: 0; overflow: hidden; }
.rm-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 12px; white-space: nowrap; background: rgba(120, 160, 210, 0.12); border: 1px solid rgba(120, 160, 210, 0.2); }
.rm-chip i { width: 7px; height: 7px; border-radius: 50%; background: #7f93ab; }
.rm-chip[data-tone="ok"] i { background: #35c46a; box-shadow: 0 0 8px #35c46a; }
.rm-chip[data-tone="warn"] i { background: #f5c518; box-shadow: 0 0 8px #f5c518; }
.rm-chip[data-tone="bad"] { color: #ff8f8f; border-color: rgba(255, 77, 79, 0.4); }
.rm-chip[data-tone="bad"] i { background: #ff4d4f; box-shadow: 0 0 8px #ff4d4f; }
.rm-chip-level { color: var(--lv); font-weight: 600; border-color: color-mix(in srgb, var(--lv) 55%, transparent); background: color-mix(in srgb, var(--lv) 14%, transparent); }
.rm-chip-level i { background: var(--lv); box-shadow: 0 0 8px var(--lv); }
.rm-chip-pending { font: inherit; font-size: 12px; cursor: pointer; color: #f5c518; font-weight: 600; border-color: rgba(245, 197, 24, 0.6); background: rgba(245, 197, 24, 0.14); animation: rm-pending 1.4s ease-in-out infinite; }
.rm-chip-pending i { background: #f5c518; box-shadow: 0 0 8px #f5c518; }
.rm-chip-pending:hover { background: rgba(245, 197, 24, 0.26); }
@keyframes rm-pending { 50% { box-shadow: 0 0 0 4px rgba(245, 197, 24, 0.18); border-color: rgba(245, 197, 24, 1); } }
/* 预警升到橙色、红色时，画面四周闪一圈等级色（只是提醒，不挡操作） */
#rescue-map-flash { position: fixed; inset: 0; z-index: 70; pointer-events: none; box-shadow: inset 0 0 0 4px var(--lv), inset 0 0 90px 10px var(--lv); opacity: 0; animation: rm-flash 1s ease-in-out 4; }
@keyframes rm-flash { 0%, 100% { opacity: 0; } 40%, 60% { opacity: 0.85; } }
.rm-tools { display: flex; gap: 4px; }
.rm-tools button {
  font: inherit; font-size: 12px; color: #cfe1f5; cursor: pointer; padding: 6px 11px; border-radius: 8px; white-space: nowrap;
  border: 1px solid rgba(120, 160, 210, 0.22); background: rgba(120, 160, 210, 0.08);
}
.rm-tools button:hover { background: rgba(79, 209, 255, 0.16); border-color: rgba(79, 209, 255, 0.5); }
.rm-tools button[aria-pressed="true"] { border-color: rgba(79, 209, 255, 0.6); color: #4fd1ff; }
.rm-tools .rm-more { font-size: 14px; line-height: 1; padding: 5px 10px; letter-spacing: -0.1em; }
/* “更多”菜单：挂在 body 上（顶栏在 DSH 会话组件下层，菜单要盖住聊天区） */
#rescue-map-menu {
  position: fixed; top: 60px; right: 16px; z-index: 60; min-width: 176px; padding: 6px; display: flex; flex-direction: column; gap: 2px;
  color: #dbe8f5; font: 13px/1.45 system-ui, -apple-system, 'Segoe UI', 'Noto Sans CJK SC', 'PingFang SC', sans-serif; ${CARD}
  transform-origin: top right; transform: translateY(-8px) scale(0.98); opacity: 0; visibility: hidden; pointer-events: none;
  transition: transform 0.18s ease, opacity 0.18s ease, visibility 0s linear 0.18s;
}
#rescue-map-menu[data-open] { transform: none; opacity: 1; visibility: visible; pointer-events: auto; transition: transform 0.18s ease, opacity 0.18s ease, visibility 0s; }
#rescue-map-menu button { display: flex; align-items: center; justify-content: space-between; gap: 16px; font: inherit; color: inherit; text-align: left; cursor: pointer; padding: 7px 10px; border: 0; border-radius: 8px; background: transparent; }
#rescue-map-menu button:hover, #rescue-map-menu button:focus-visible { background: rgba(79, 209, 255, 0.14); outline: none; }
#rescue-map-menu button::after { content: ''; width: 14px; color: #4fd1ff; text-align: center; }
#rescue-map-menu button[aria-pressed="true"]::after { content: '✓'; }
#rescue-map-menu hr { width: 100%; margin: 4px 0; border: 0; border-top: 1px solid rgba(120, 160, 210, 0.16); }

.rm-card { ${CARD} }
.rm-left, .rm-right { box-sizing: border-box; position: absolute; top: var(--rm-top); overflow-y: auto; scrollbar-width: thin; scrollbar-color: rgba(120, 160, 210, 0.35) transparent;
  transition: transform var(--rm-slide-t) var(--rm-ease), visibility 0s; }
.rm-left { left: 16px; bottom: 16px; width: 340px; padding: 6px 14px 14px; }
/* 智能体卡片：在聊天区上方，按内容高度，最多占三分之一屏；聊天区隐藏时可以往下长 */
.rm-right { right: 16px; width: var(--rm-chat-w); max-height: 34vh; padding: 2px 14px 8px; }
html.rm-hide-chat .rm-right { max-height: calc(100vh - var(--rm-top) - 16px); }
html.rm-hide-left .rm-left, html.rm-hide-right .rm-right { visibility: hidden; pointer-events: none; transition: transform var(--rm-slide-t) var(--rm-ease), visibility 0s linear var(--rm-slide-t); }
html.rm-hide-left .rm-left { transform: translateX(calc(-100% - 24px)); }
html.rm-hide-right .rm-right { transform: translateX(calc(100% + 24px)); }
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
.rm-modal-overlay { position: fixed; inset: 0; z-index: 40; display: flex; align-items: center; justify-content: center; background: rgba(3, 8, 18, 0.6); padding: 16px; box-sizing: border-box; animation: rm-fade-in 0.2s ease; }
.rm-modal-overlay .rm-modal { animation: rm-pop-in 0.3s var(--rm-ease, ease); }
.rm-modal-overlay.rm-closing { animation: rm-fade-out 0.2s ease forwards; pointer-events: none; }
.rm-modal-overlay.rm-closing .rm-modal { animation: rm-pop-out 0.2s ease-in forwards; }
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

#rescue-map-legend { position: absolute; top: var(--rm-top); right: var(--rm-r); width: min(300px, calc(100vw - 24px)); padding: 10px 12px; max-height: calc(100vh - 100px); overflow: auto; ${CARD} z-index: 5;
  transition: transform var(--rm-slide-t) var(--rm-ease), opacity var(--rm-slide-t) ease, right var(--rm-slide-t) var(--rm-ease), visibility 0s; }
#rescue-map-legend:not([data-open]) { transform: translateX(calc(100% + 24px)); opacity: 0; visibility: hidden; pointer-events: none;
  transition: transform var(--rm-slide-t) var(--rm-ease), opacity var(--rm-slide-t) ease, right var(--rm-slide-t) var(--rm-ease), visibility 0s linear var(--rm-slide-t); }
#rescue-map-legend h4 { margin: 0 0 6px; font: 600 12px system-ui, sans-serif; color: #7fb2e6; letter-spacing: 0.14em; }

.rm-label { position: absolute; left: 0; top: 0; white-space: nowrap; font: 11px/1 system-ui, sans-serif; transform-origin: 0 0; margin: -6px 0 0 4px; }
.rm-label[data-kind="flow"], .rm-label[data-kind="section"], .rm-label[data-kind="note"] { font-size: 10px; opacity: 0.85; }
.rm-label.rm-hover { color: #4fd1ff !important; text-decoration: underline; text-underline-offset: 2px; }
.rm-label.rm-picked { animation: rm-picked 0.9s ease-out; }
@keyframes rm-picked { 0% { background: rgba(79, 209, 255, 0.55); box-shadow: 0 0 0 4px rgba(79, 209, 255, 0.55); } 100% { background: transparent; box-shadow: 0 0 0 4px transparent; } }
#rescue-map-toast { position: fixed; left: calc((var(--rm-l) + 100vw - var(--rm-r)) / 2); bottom: 28px; transform: translateX(-50%); z-index: 12; padding: 8px 14px; border-radius: 10px; font-size: 13px; color: #eaf4ff; pointer-events: none; ${CARD} }
/* 卡片在下方时提示挪到顶栏下面，免得被卡片盖住 */
${ON}:has(${CENTER} :is([data-approval-key], [data-question-key])) #rescue-map-toast { bottom: auto; top: calc(var(--rm-top) + 8px); }
#rescue-map-toast[hidden] { display: none; }
#rescue-map-toast:not([hidden]) { animation: rm-fade-in 0.25s ease; }
#rescue-map-toast[data-tone="alert"] { border-color: rgba(255, 138, 31, 0.7) !important; color: #ffd2a8; font-weight: 600; }
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
.rm-fig-route_local { background: #2f6fd6; }
/* 灾害推演、路径规划的来源只用颜色区分，和地图上的线同色：路径规划 外部服务绿、智能体蓝；灾害推演 外部服务紫、智能体红橙 */
.rm-src { margin-top: 2px; }
.rm-src[data-kind="route"][data-source="service"] { color: #7ad9a1; }
.rm-src[data-kind="route"][data-source="ours"] { color: #7fb2e6; }
.rm-src[data-kind="fire"][data-source="service"] { color: #b9a4ff; }
.rm-src[data-kind="fire"][data-source="ours"] { color: #ff9a7a; }
.rm-evac-route.rm-src[data-tone="bad"] { font-weight: 600; border-left: 3px solid #ff4d4f; padding-left: 6px; }
.rm-fire-sim.rm-src { border-left: 3px solid currentColor; padding-left: 8px; }
.rm-fire-sim.rm-src li { color: #dbe8f5; }
.rm-src-key { display: flex; align-items: center; flex-wrap: wrap; gap: 2px 4px; }
.rm-swatch { display: inline-block; width: 16px; height: 4px; border-radius: 2px; margin-left: 6px; }
.rm-swatch[data-kind="route"][data-source="service"] { background: #35c46a; }
.rm-swatch[data-kind="route"][data-source="ours"] { background: #4d8ff0; }
.rm-swatch[data-kind="fire"][data-source="service"] { background: #a78bfa; }
.rm-swatch[data-kind="fire"][data-source="ours"] { background: linear-gradient(90deg, #ff4d4f, #ffb020); }
.rm-fig-contaminated { background: rgba(216,58,61,.85); }
.rm-fig-fire_spread { background: rgba(124,92,214,.88); font-size: 10px; }
.rm-fire-sim { font-size: 12px; }
.rm-fire-sim ul { margin: 4px 0; padding-left: 16px; }
.rm-fire-sim li { margin: 2px 0; }
.rm-fire-sim li[data-danger="1"] b { color: #f052e6; }
.rm-fire-sim li b { color: #b9a4ff; font-weight: 600; }
.rm-fire-swatch { display: inline-block; width: 14px; height: 3px; vertical-align: middle; margin: 0 4px 0 0; border-radius: 2px; }
.rm-fire-replay { display: flex; align-items: center; gap: 6px; margin: 6px 0; }
.rm-fire-replay input[type="range"] { flex: 1; accent-color: #a78bfa; }
.rm-fire-replay button { font-size: 11px; padding: 2px 8px; border-radius: 6px; border: 1px solid rgba(255,255,255,.25); background: rgba(255,255,255,.06); color: inherit; cursor: pointer; }
.rm-fire-replay output { min-width: 64px; text-align: right; font-variant-numeric: tabular-nums; opacity: .9; }
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
  .rm-chip:not(.rm-chip-level) { display: none; }
  .rm-title span { display: none; }
}
@media (max-width: 800px) {
  ${ON} { --rm-l: 12px; --rm-r: 12px; }
  .rm-left, .rm-right { display: none; }
  ${ON} ${CENTER} { left: 12px; right: 12px; width: auto; top: auto; bottom: 12px; height: min(60vh, 560px); }
  ${ON}.rm-hide-chat ${CENTER} { right: 12px; bottom: calc(-1 * min(60vh, 560px) - 24px); }
  .rm-clock, .rm-title span { display: none; }
}
/* 关掉动态效果（localStorage['rescue-map:no-motion'] = '1'）：不滑动，直接显示/隐藏。
   不跟随系统的“减少动态效果”：大屏要滑动提示变化，而 Ubuntu 上关掉桌面动画时 Firefox 就报告“减少动态效果”（2026-11-18 演示）。
   只作用于自绘部分和面板外壳，不动 DSH 组件自己的动画 */
html.rm-no-motion :is(#rescue-hud, #rescue-hud *, .rm-ghost, .rm-ghost *, .rm-modal-overlay, .rm-modal-overlay *, #rescue-map-menu, ${CENTER}, ${SIDEBAR}, ${RIGHT}),
html.rm-no-motion ${CENTER} :is([data-approval-key], [data-question-key]) > * {
  transition-duration: 0s !important; transition-delay: 0s !important; animation: none !important;
}
html.rm-no-motion #rescue-map-flash { animation: none !important; opacity: 0.6; }
`

export interface HudHandlers {
  legend(): Legend | null
  title(): string
  /** 面板显示/隐藏之后调用，让地图重新适配 */
  layoutChanged(): void
  focus(name: string): void
  /** 火灾推演层的回放时刻（分钟）；null 显示全部 */
  fireSpreadTime(t: number | null): void
  tilt(): number
  setTilt(deg: number): void
  reset(): void
}

export interface Hud {
  /** 在地图下方显示一条短提示，几秒后消失；tone 为 alert 时醒目一些 */
  toast(text: string, tone?: 'alert'): void
  /** 聊天区被隐藏时把它显示出来（点选引用要往输入框里写） */
  showChat(): void
  /** 全屏地图 = 左栏、智能体卡片、对话区都收起。返回之前是不是全屏 */
  setFullscreen(on: boolean): boolean
  setConnected(ok: boolean): void
  setState(s: StateData | null, staleS: number, failed: boolean): void
  dispose(): void
}

const root = document.documentElement
const PANEL_CLASSES = ['rm-hide-left', 'rm-hide-right', 'rm-hide-chat'] as const
const CARD_SEL = '[data-approval-key], [data-question-key]'
/** 预警等级的先后（只用来判断“升了没有”，等级本身来自监听器） */
const LEVEL_RANK: Record<string, number> = { none: 0, blue: 1, yellow: 2, orange: 3, red: 4 }
/** 事件转入平静后多久自动回到全屏地图（界面参数，占位：2 分钟，看演示效果再调） */
const AUTO_FULL_MS = 120_000

/** 操作员在浏览器里关掉了动态效果（见 CSS 的 rm-no-motion）；默认不关，也不跟随系统设置 */
export function noMotion(): boolean {
  try {
    return localStorage.getItem('rescue-map:no-motion') === '1'
  } catch {
    return false
  }
}
const reducedMotion = (): boolean => root.classList.contains('rm-no-motion')

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

/**
 * 人工确认卡片、提问卡片关掉时往下滑出：卡片是 DSH 的组件，关掉就直接从 DOM 里拿走了，
 * 这里在它被拿走时放一个克隆，在原位置往下滑出后删掉（只是画面，克隆不能点）。
 * 同一批变动里又出现了同一个键的卡片（DSH 重画）就不放克隆。返回停止函数。
 */
function watchCards(): () => void {
  const host = document.getElementById('root')
  if (!host) return () => {}
  const widths = new WeakMap<Element, number>()
  const ro = new ResizeObserver((entries) => {
    for (const e of entries) if (e.contentRect.width > 0) widths.set(e.target, (e.target as HTMLElement).getBoundingClientRect().width)
  })
  const cardsIn = (n: Node): Element[] => {
    if (!(n instanceof Element)) return []
    return [...(n.matches(CARD_SEL) ? [n] : []), ...n.querySelectorAll(CARD_SEL)]
  }
  const keyOf = (e: Element): string => `${e.getAttribute('data-approval-key') ?? ''}|${e.getAttribute('data-question-key') ?? ''}`
  host.querySelectorAll(CARD_SEL).forEach((e) => ro.observe(e))
  const mo = new MutationObserver((muts) => {
    const added = muts.flatMap((m) => [...m.addedNodes].flatMap(cardsIn))
    added.forEach((e) => ro.observe(e))
    const addedKeys = new Set(added.map(keyOf))
    for (const gone of muts.flatMap((m) => [...m.removedNodes].flatMap(cardsIn))) {
      ro.unobserve(gone)
      const w = widths.get(gone)
      if (!w || addedKeys.has(keyOf(gone)) || reducedMotion() || !root.classList.contains('rm-on')) continue
      const ghost = h('div', { class: 'rm-ghost', 'aria-hidden': 'true', style: `width:${Math.round(w)}px` })
      const copy = gone.cloneNode(true) as Element
      copy.removeAttribute('id')
      ghost.append(copy)
      document.body.append(ghost)
      window.setTimeout(() => ghost.remove(), 340)
    }
  })
  mo.observe(host, { childList: true, subtree: true })
  return () => {
    mo.disconnect()
    ro.disconnect()
  }
}

export function mountHud(hd: HudHandlers): Hud {
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = CSS
  document.head.appendChild(style)
  root.classList.add('rm-on')
  root.classList.toggle('rm-no-motion', noMotion())
  const restoreTheme = forceDark()
  const stopCards = watchCards()

  const hud = document.createElement('div')
  hud.id = HUD_ID
  // 放在 #root 前面：DSH 的抽屉和弹窗（#root 里）要盖在自绘面板上面
  document.body.insertBefore(hud, document.getElementById('root'))
  const panels: PanelsHandle = mountPanels(
    hud,
    hd.title(),
    (name) => hd.focus(name),
    (t) => hd.fireSpreadTime(t),
    () => void setFull(false),
  )
  const tools = hud.querySelector('.rm-tools') as HTMLElement
  // 智能体卡片的高度决定聊天区从哪里开始（CSS 变量 --rm-agent-h）
  const agentCard = hud.querySelector('.rm-right') as HTMLElement
  const ro = new ResizeObserver(() => {
    const hgt = agentCard.offsetHeight
    root.style.setProperty('--rm-agent-h', `${hgt}px`)
    requestAnimationFrame(() => hd.layoutChanged())
  })
  ro.observe(agentCard)
  const relayout = (): void => void requestAnimationFrame(() => hd.layoutChanged())
  let slideTimer = 0
  const markSliding = (): void => {
    root.classList.add('rm-sliding')
    window.clearTimeout(slideTimer)
    slideTimer = window.setTimeout(() => root.classList.remove('rm-sliding'), SLIDE_MS + 80)
  }

  // ── 操作按钮：顶栏只放常用的（全屏地图、复位），其余收进“»”菜单 ──
  const menu = h('div', { id: 'rescue-map-menu', role: 'menu', 'aria-label': '更多操作' })
  document.body.appendChild(menu)
  const toolBtn = (text: string, title: string, onClick: () => void): HTMLButtonElement => {
    const b = h('button', { type: 'button', title }, text)
    b.addEventListener('click', onClick)
    tools.appendChild(b)
    return b
  }
  const menuItem = (text: string, onClick: () => void): HTMLButtonElement => {
    const b = h('button', { type: 'button', role: 'menuitemcheckbox' }, text)
    b.addEventListener('click', onClick)
    menu.appendChild(b)
    return b
  }

  const isFull = (): boolean => PANEL_CLASSES.every((c) => root.classList.contains(c))
  const fullBtn = toolBtn('全屏地图', '收起两侧面板，只看地图（快捷键 F）', () => setFull(!isFull()))
  toolBtn('复位', '地图回到初始视图', () => hd.reset())
  const moreBtn = toolBtn('»', '更多操作', () => (menu.hasAttribute('data-open') ? closeMenu() : openMenu()))
  moreBtn.classList.add('rm-more')
  moreBtn.setAttribute('aria-haspopup', 'menu')

  const panelItems = (
    [
      ['rm-hide-left', '矿井信息栏'],
      ['rm-hide-right', '智能体卡片'],
      ['rm-hide-chat', '对话区'],
    ] as const
  ).map(([cls, label]) => ({
    cls,
    b: menuItem(label, () => {
      markSliding()
      root.classList.toggle(cls)
      sync()
      relayout()
    }),
  }))
  menu.appendChild(h('hr'))
  const drawerItem = menuItem('会话列表', () => {
    markSliding()
    root.classList.toggle('rm-drawer')
    sync()
    closeMenu()
  })

  const legendData = hd.legend()
  const legendBox = h('div', { id: 'rescue-map-legend' })
  let legendItem: HTMLButtonElement | null = null
  if (legendData) {
    legendBox.replaceChildren(h('h4', null, '图例'), renderLegend(legendData, true))
    hud.appendChild(legendBox)
    legendItem = menuItem('图例', () => {
      markSliding()
      legendBox.toggleAttribute('data-open')
      sync()
    })
  }
  const tiltItem = menuItem('倾斜视角', () => {
    hd.setTilt(hd.tilt() > 1 ? 0 : 40)
    sync()
  })

  function sync(): void {
    const full = isFull()
    fullBtn.textContent = full ? '展开面板' : '全屏地图'
    fullBtn.title = full ? '显示两侧面板（快捷键 F）' : '收起两侧面板，只看地图（快捷键 F）'
    fullBtn.setAttribute('aria-pressed', String(full))
    for (const it of panelItems) it.b.setAttribute('aria-pressed', String(!root.classList.contains(it.cls)))
    drawerItem.setAttribute('aria-pressed', String(root.classList.contains('rm-drawer')))
    legendItem?.setAttribute('aria-pressed', String(legendBox.hasAttribute('data-open')))
    tiltItem.setAttribute('aria-pressed', String(hd.tilt() > 1))
  }
  function setFull(on: boolean): boolean {
    const was = isFull()
    if (was !== on) markSliding()
    for (const c of PANEL_CLASSES) root.classList.toggle(c, on)
    sync()
    if (was !== on) relayout()
    return was
  }

  const onDocDown = (e: PointerEvent): void => {
    const t = e.target as Node
    if (!menu.contains(t) && !moreBtn.contains(t)) closeMenu()
  }
  const onKey = (e: KeyboardEvent): void => {
    if (e.key === 'Escape' && menu.hasAttribute('data-open')) {
      closeMenu()
      moreBtn.focus()
    }
  }
  function openMenu(): void {
    sync()
    menu.setAttribute('data-open', '')
    moreBtn.setAttribute('aria-expanded', 'true')
    document.addEventListener('pointerdown', onDocDown, true)
    document.addEventListener('keydown', onKey, true)
  }
  function closeMenu(): void {
    menu.removeAttribute('data-open')
    moreBtn.setAttribute('aria-expanded', 'false')
    document.removeEventListener('pointerdown', onDocDown, true)
    document.removeEventListener('keydown', onKey, true)
  }

  // 快捷键：F 切换全屏地图；Esc 依次收起菜单、会话列表、图例。光标在输入框里、按着修饰键时不生效，也不抢 DSH 自己的 Esc（没有可收起的就不拦）
  const typing = (t: EventTarget | null): boolean =>
    t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || !!t.closest('[contenteditable="true"], [role="textbox"]'))
  const onShortcut = (e: KeyboardEvent): void => {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return
    if ((e.key === 'f' || e.key === 'F') && !e.shiftKey) {
      e.preventDefault()
      setFull(!isFull())
    } else if (e.key === 'Escape') {
      if (root.classList.contains('rm-drawer')) root.classList.remove('rm-drawer')
      else if (legendBox.hasAttribute('data-open')) legendBox.removeAttribute('data-open')
      else return
      markSliding()
      e.preventDefault()
      sync()
    }
  }
  document.addEventListener('keydown', onShortcut)

  // 启动时是全屏地图（2026-11-18）：面板等来了预警再滑出来（index.ts），或者操作员点“展开面板”
  setFull(true)

  const toastEl = document.createElement('div')
  toastEl.id = 'rescue-map-toast'
  toastEl.hidden = true
  hud.appendChild(toastEl)
  let toastTimer = 0

  // 预警升到橙色、红色时闪一圈（第一次只记下当前等级，刚打开页面不闪）
  const flash = h('div', { id: 'rescue-map-flash', 'aria-hidden': 'true' })
  let lastRank: { id: string; rank: number } | null | undefined
  const watchLevel = (s: StateData): void => {
    const inc = s.incident
    const now = inc ? { id: inc.id, rank: LEVEL_RANK[inc.level ?? ''] ?? 0 } : null
    const before = lastRank
    lastRank = now
    if (before === undefined || !now || now.rank < LEVEL_RANK.orange!) return
    const prev = before && before.id === now.id ? before.rank : 0
    if (now.rank <= prev) return
    flash.style.setProperty('--lv', now.rank >= LEVEL_RANK.red! ? '#ff4d4f' : '#ff8a1f')
    flash.remove()
    void flash.offsetWidth
    document.body.appendChild(flash)
    window.setTimeout(() => flash.remove(), 4200)
  }

  // 事件稳定或关闭、没有待确认和待回答的卡片，持续 AUTO_FULL_MS 后自动回到全屏地图（只在“转入平静”那一刻开始计时；
  // 计时中事件又有动静就取消；操作员之后自己展开的不再收起，直到下一次转入平静）
  let calm: boolean | undefined
  let calmTimer = 0
  const cardsShown = (): boolean => !!document.querySelector(`${CENTER} :is(${CARD_SEL})`)
  const watchCalm = (s: StateData): void => {
    const inc = s.incident
    const now = (!inc || inc.status === 'stable' || inc.status === 'closed') && (inc?.pendingApprovals.length ?? 0) === 0 && !/^waiting_/.test(s.agent.state)
    const was = calm
    calm = now
    if (!now) {
      window.clearTimeout(calmTimer)
      calmTimer = 0
      return
    }
    if (was !== false) return // 第一次（刚打开页面）或一直平静：不计时
    window.clearTimeout(calmTimer)
    calmTimer = window.setTimeout(() => {
      calmTimer = 0
      if (!calm || cardsShown() || isFull()) return
      setFull(true)
      hudApi.toast('事件已稳定，回到全屏地图')
    }, AUTO_FULL_MS)
  }

  let connected = false
  const hudApi: Hud = {
    toast(text, tone) {
      toastEl.textContent = text
      if (tone) toastEl.dataset.tone = tone
      else delete toastEl.dataset.tone
      toastEl.hidden = true // 连续两条时重新播放出现的动画
      void toastEl.offsetWidth
      toastEl.hidden = false
      window.clearTimeout(toastTimer)
      toastTimer = window.setTimeout(() => (toastEl.hidden = true), tone === 'alert' ? 6000 : 3000)
    },
    showChat() {
      if (!root.classList.contains('rm-hide-chat')) return
      markSliding()
      root.classList.remove('rm-hide-chat')
      sync()
      relayout()
    },
    setFullscreen: setFull,
    setConnected(ok) {
      connected = ok
      hud.dataset.channel = String(connected)
    },
    setState(s, staleS, failed) {
      panels.update(s, staleS, failed)
      if (s && !failed) {
        watchLevel(s)
        watchCalm(s)
      }
    },
    dispose() {
      ro.disconnect()
      window.clearTimeout(calmTimer)
      window.clearTimeout(slideTimer)
      flash.remove()
      document.removeEventListener('keydown', onShortcut)
      stopCards()
      closeMenu()
      menu.remove()
      root.style.removeProperty('--rm-agent-h')
      panels.dispose()
      hud.remove()
      style.remove()
      restoreTheme()
      root.classList.remove('rm-on', 'rm-drawer', 'rm-sliding', 'rm-no-motion', ...PANEL_CLASSES)
    },
  }
  return hudApi
}

/** 地图加载失败时退回 DSH 原界面。 */
export function restoreNative(): void {
  document.getElementById(STYLE_ID)?.remove()
  document.getElementById(HUD_ID)?.remove()
  root.classList.remove('rm-on', 'rm-drawer')
}

/** 元素在页面上的布局位置（不算 transform：面板滑动途中拿到的也是最终位置） */
function layoutRect(e: HTMLElement): { left: number; top: number; right: number; bottom: number; width: number; height: number } {
  let x = 0
  let y = 0
  for (let n: HTMLElement | null = e; n; n = n.offsetParent as HTMLElement | null) {
    x += n.offsetLeft
    y += n.offsetTop
  }
  return { left: x, top: y, right: x + e.offsetWidth, bottom: y + e.offsetHeight, width: e.offsetWidth, height: e.offsetHeight }
}

/**
 * 悬浮面板盖住的边（像素）：左栏、右栏（智能体卡片 + 对话区；窄屏时对话区在底部）；顶部给顶栏留位置。
 * 收起的面板不算；按面板展开后的最终位置算（面板正在滑动时地图就飞向最终的空白区域）。下方有确认卡片时底边算到卡片顶上。
 */
export function panelInsets(): Insets {
  const w = window.innerWidth
  const hgt = window.innerHeight
  const rect = (sel: string, hiddenCls: string): ReturnType<typeof layoutRect> | null => {
    if (root.classList.contains(hiddenCls)) return null
    const e = document.querySelector<HTMLElement>(sel)
    if (!e || getComputedStyle(e).display === 'none') return null
    const r = layoutRect(e)
    return r.width > 0 && r.height > 0 ? r : null
  }
  const left = rect('.rm-left', 'rm-hide-left')
  const right = rect('.rm-right', 'rm-hide-right')
  // 会话列用 right/bottom 滑动（见 CSS），布局位置在滑动途中会变：按展开后的位置算（右边或窄屏时的底部，边距同 CSS）
  const chatBox = rect(CENTER, 'rm-hide-chat')
  const chatAtBottom = !!chatBox && (window.matchMedia?.('(max-width: 800px)').matches ?? false)
  const chat = chatBox && (chatAtBottom ? { ...chatBox, top: hgt - 12 - chatBox.height } : { ...chatBox, left: w - 16 - chatBox.width })
  const rightEdge = Math.min(right ? right.left : w, chat && !chatAtBottom ? chat.left : w)
  // 画面下方的确认卡片、提问卡片也算盖住底边：飞过去的位置落在卡片上方
  let cardTop = hgt
  for (const c of document.querySelectorAll<HTMLElement>(`${CENTER} :is(${CARD_SEL})`)) {
    const r = c.getBoundingClientRect()
    if (r.width > 0 && r.height > 0) cardTop = Math.min(cardTop, r.top)
  }
  return {
    left: left ? left.right : 0,
    right: w - rightEdge,
    top: 60,
    bottom: Math.max(chat && chatAtBottom ? hgt - chat.top : 0, hgt - cardTop + 8),
  }
}
