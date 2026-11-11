/**
 * 人工确认卡片的排版：DSH 把确认说明放进一个不保留换行的标题块，多行说明（标题、确认人、要点、把关事项、核对、依据）
 * 会挤成一大段粗体。这里补一段全局样式保留换行，第一行（要做的事）加粗，其余用正常字重。
 * 不依赖地图（没有地图包时也生效）；只用 DSH 卡片上的稳定属性（data-approval-key、role="group"），不用哈希类名。
 * 升级 DSH 要重核卡片结构（@deepseek-ai/dsh-client-ui-approval 的 ApprovalPanel）。
 */
const STYLE_ID = 'rescue-approval-style'

const CSS = `
[data-approval-key] [role="group"] > div:first-child { white-space: pre-line; font-weight: 400; font-size: 14px; line-height: 22px; }
[data-approval-key] [role="group"] > div:first-child::first-line { font-weight: 600; font-size: 15px; }
`

export function mountApprovalStyle(): () => void {
  if (document.getElementById(STYLE_ID)) return () => {}
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = CSS
  document.head.appendChild(style)
  return () => style.remove()
}
