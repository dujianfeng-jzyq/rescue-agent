/**
 * “最近记录”的弹窗：点一条记录，从 /rescue-map/record 取只读视图显示。报告按 Markdown 子集显示（标题、列表、加粗），
 * 结果列标量字段，证据只给汇总（证据很大）。上级记录可以点开继续看。前端不算、不判断，只显示。
 */
import { h } from './panels.ts'

interface RecordView {
  id: string
  kindName: string
  title: string
  panel: string | null
  asOf: string
  parents: string[]
  body: { type: 'markdown'; text: string; from?: string } | { type: 'fields'; rows: { key: string; value: string }[]; warnings: string[] }
}

let overlay: HTMLElement | null = null

function close(): void {
  overlay?.remove()
  overlay = null
  document.removeEventListener('keydown', onKey, true)
}

function onKey(e: KeyboardEvent): void {
  if (e.key === 'Escape') {
    e.stopPropagation()
    close()
  }
}

/** Markdown 子集：【标题】行、“- ”列表、**加粗**；图片行（图表）略去，说明在完整报告里 */
function markdown(text: string): HTMLElement {
  const box = h('div', { class: 'rm-md' })
  let list: HTMLElement | null = null
  for (const raw of text.split('\n')) {
    const line = raw.trimEnd()
    if (/^!\[.*\]\(.*\)$/.test(line.trim())) continue
    if (/^\s*- /.test(line)) {
      list ??= box.appendChild(h('ul'))
      list.append(inline(line.replace(/^\s*- /, ''), 'li'))
      continue
    }
    list = null
    if (!line.trim()) continue
    box.append(inline(line.replace(/^#+\s*/, ''), /^(#|【)/.test(line) ? 'h4' : 'p'))
  }
  return box
}

function inline(text: string, tag: 'li' | 'p' | 'h4'): HTMLElement {
  const e = h(tag)
  text.split(/(\*\*[^*]+\*\*)/).forEach((part) => e.append(part.startsWith('**') && part.endsWith('**') ? h('b', null, part.slice(2, -2)) : part))
  return e
}

export async function openRecord(id: string): Promise<void> {
  close()
  const body = h('div', { class: 'rm-modal-body' }, h('div', { class: 'rm-empty' }, '读取中…'))
  const titleEl = h('b', null, id)
  const meta = h('div', { class: 'rm-modal-meta' })
  const btn = h('button', { type: 'button', class: 'rm-modal-close', 'aria-label': '关闭' }, '×')
  btn.addEventListener('click', close)
  const dialog = h('div', { class: 'rm-modal', role: 'dialog', 'aria-modal': 'true' }, h('div', { class: 'rm-modal-head' }, titleEl, btn), meta, body)
  overlay = h('div', { class: 'rm-modal-overlay' }, dialog)
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) close()
  })
  document.body.append(overlay)
  document.addEventListener('keydown', onKey, true)
  try {
    const u = new URL('rescue-map/record', document.baseURI)
    u.searchParams.set('id', id)
    const r = await fetch(u, { credentials: 'same-origin', cache: 'no-store' })
    const v = (await r.json()) as RecordView & { error?: string }
    if (!r.ok) throw new Error(v.error ?? `HTTP ${r.status}`)
    titleEl.textContent = `${v.title || v.kindName}`
    meta.replaceChildren(
      h('code', null, v.id),
      h('span', null, `${v.kindName}${v.panel ? ` · ${v.panel} 工作面` : ''} · 矿井时间 ${v.asOf.replace('T', ' ').slice(0, 19)}`),
      ...(v.parents.length
        ? [
            h('span', null, '依据：'),
            ...v.parents.map((p) => {
              const a = h('button', { type: 'button', class: 'rm-modal-link' }, p)
              a.addEventListener('click', () => void openRecord(p))
              return a
            }),
          ]
        : []),
    )
    if (v.body.type === 'markdown') body.replaceChildren(...(v.body.from ? [h('p', { class: 'rm-note' }, `按这条结果生成的报告 ${v.body.from}：`)] : []), markdown(v.body.text))
    else
      body.replaceChildren(
        v.body.rows.length ? h('dl', { class: 'rm-kv rm-modal-kv' }, ...v.body.rows.flatMap((x) => [h('dt', null, x.key), h('dd', null, x.value)])) : h('div', { class: 'rm-empty' }, '没有可显示的字段'),
        ...v.body.warnings.map((w) => h('p', { class: 'rm-note' }, `说明：${w}`)),
      )
  } catch (e) {
    body.replaceChildren(h('div', { class: 'rm-empty' }, `读取失败：${(e as Error).message}`))
  }
}
