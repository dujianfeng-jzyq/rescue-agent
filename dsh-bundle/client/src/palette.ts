/** 配色：深浅两套。地图和图例共用，保证图例的符号颜色和地图上一致。 */

export interface Theme {
  bg: string
  roadway: string
  note: string
  facility: string
  flow: string
  /** 实心填充：巷道本体（ACI 252）、设备（34、249）、其他 */
  fill: Record<string, { color: string; opacity: number }>
}

export const THEMES: Record<'dark' | 'light', Theme> = {
  dark: {
    bg: '#0b1522',
    roadway: '#9fb3c8',
    note: '#43586f',
    facility: '#5fc7e8',
    flow: '#7ad9a1',
    fill: {
      '252': { color: '#223a54', opacity: 0.95 },
      '34': { color: '#d98b2b', opacity: 0.95 },
      '249': { color: '#4b78b8', opacity: 0.95 },
      default: { color: '#35506b', opacity: 0.9 },
    },
  },
  light: {
    bg: '#eef2f6',
    roadway: '#34495e',
    note: '#b5c1cd',
    facility: '#1b7fa8',
    flow: '#2d9d62',
    fill: {
      '252': { color: '#c9d3de', opacity: 0.95 },
      '34': { color: '#e08a1e', opacity: 0.95 },
      '249': { color: '#5b86c7', opacity: 0.95 },
      default: { color: '#aebccb', opacity: 0.9 },
    },
  },
}

/** AutoCAD 索引色 1–6 保留作者的意图（红、黄、绿、青、蓝、品红）；其余按图层角色上色。 */
export const ACI_TINT: Record<number, { dark: string; light: string }> = {
  1: { dark: '#ff6b6b', light: '#d6393d' },
  2: { dark: '#ffd166', light: '#b58900' },
  3: { dark: '#5fd68a', light: '#1f9d55' },
  4: { dark: '#5fd3e8', light: '#138aa6' },
  5: { dark: '#6aa8ff', light: '#2f6fcf' },
  6: { dark: '#e08aff', light: '#9b3fc4' },
}

export function fillStyle(theme: Theme, aci: string): { color: string; opacity: number } {
  return theme.fill[aci] ?? theme.fill.default!
}
