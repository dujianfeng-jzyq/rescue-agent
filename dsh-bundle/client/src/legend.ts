import { fillStyle, THEMES } from './palette.ts'
import type { Legend } from './types.ts'

const NS = 'http://www.w3.org/2000/svg'

function el<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string | number>): SVGElementTagNameMap[K] {
  const e = document.createElementNS(NS, tag)
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v))
  return e
}

function pathD(flat: number[], height: number, close: boolean): string {
  let d = ''
  for (let i = 0; i + 1 < flat.length; i += 2) d += `${i === 0 ? 'M' : 'L'}${flat[i]} ${height - flat[i + 1]!}`
  return close ? `${d}Z` : d
}

/** 把导出的图例画成 SVG：y 向上翻成屏幕坐标，线用当前文字色，填充用地图同一套配色。 */
export function renderLegend(legend: Legend, dark: boolean): SVGSVGElement {
  const [w, h] = legend.size
  const theme = THEMES[dark ? 'dark' : 'light']
  const svg = el('svg', { viewBox: `0 0 ${w} ${h}`, width: '100%', role: 'img', 'aria-label': '图例' })
  for (const [aci, fills] of Object.entries(legend.fills)) {
    const st = fillStyle(theme, aci)
    for (const f of fills) {
      const d = [f.o, ...(f.h ?? [])].map((r) => pathD(r, h, true)).join(' ')
      svg.appendChild(el('path', { d, fill: st.color, 'fill-opacity': st.opacity, 'fill-rule': 'evenodd', stroke: 'none' }))
    }
  }
  for (const g of Object.values(legend.layers)) {
    for (const l of g.lines) {
      svg.appendChild(el('path', { d: pathD(l.p, h, !!l.c), fill: 'none', stroke: 'currentColor', 'stroke-width': 0.35, 'vector-effect': 'non-scaling-stroke', 'stroke-opacity': 0.85 }))
    }
  }
  for (const t of legend.labels) {
    // TEXT 的插入点是基线左端，MTEXT 是左上角
    const text = el('text', { x: t.x, y: h - t.y, 'font-size': Math.max(t.h, 2) * 0.9, fill: 'currentColor', 'dominant-baseline': t.m ? 'hanging' : 'auto' })
    text.textContent = t.text
    svg.appendChild(text)
  }
  return svg
}
