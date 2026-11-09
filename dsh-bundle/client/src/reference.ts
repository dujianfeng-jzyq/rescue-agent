/**
 * 点选引用：操作员点地图上的名称，往 DSH 的输入框里插一段引用文字（【地图：211016采面】），再接着问。
 * 人设里写明这是操作员在地图上点选的位置，名称可以直接用于 map_view。
 *
 * 输入框是 DSH 的 Lexical 编辑器（[data-composer-input]）。用 execCommand('insertText') 走它自己的输入事件，
 * 和打字等价，编辑器内部状态不会脱节；直接改 DOM 会脱节，不能那样做。
 */

export function referenceText(name: string): string {
  return `【地图：${name}】`
}

export type InsertResult = 'inserted' | 'unavailable'

export function insertIntoComposer(text: string): InsertResult {
  const el = document.querySelector<HTMLElement>('[data-composer-input]')
  if (!el || !el.isConnected || el.getClientRects().length === 0) return 'unavailable' // 输入框不在（被确认或提问卡片接管、被隐藏）
  el.focus()
  const sel = window.getSelection()
  if (sel && !el.contains(sel.anchorNode)) {
    // 光标不在输入框里：放到末尾
    const r = document.createRange()
    r.selectNodeContents(el)
    r.collapse(false)
    sel.removeAllRanges()
    sel.addRange(r)
  }
  // 前面已有文字且不是以空白结尾时，先补一个空格，引用不贴在字上
  const before = el.textContent ?? ''
  const prefix = before.length > 0 && !/\s$/.test(before) ? ' ' : ''
  return document.execCommand('insertText', false, prefix + text) ? 'inserted' : 'unavailable'
}
