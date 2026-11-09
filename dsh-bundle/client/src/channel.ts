import type { MapCommand, MapSnapshot } from './types.ts'

export interface Channel {
  close(): void
}

/** 订阅 Node 侧的地图指令：先收到快照，之后逐条指令。EventSource 自带断线重连，重连后会再收到快照。 */
export function connect(
  onSnapshot: (s: MapSnapshot) => void,
  onCommand: (c: MapCommand) => void,
  onState: (connected: boolean) => void,
): Channel {
  const es = new EventSource(new URL('rescue-map/events', document.baseURI), { withCredentials: true })
  es.addEventListener('open', () => onState(true))
  es.addEventListener('error', () => onState(false))
  es.addEventListener('snapshot', (e) => onSnapshot(JSON.parse((e as MessageEvent<string>).data) as MapSnapshot))
  es.addEventListener('cmd', (e) => onCommand((JSON.parse((e as MessageEvent<string>).data) as { cmd: MapCommand }).cmd))
  return { close: () => es.close() }
}

export async function loadGeometry(): Promise<import('./types.ts').Geometry> {
  const r = await fetch(new URL('rescue-map/geometry.json', document.baseURI), { credentials: 'same-origin' })
  if (!r.ok) throw new Error(`几何数据加载失败：${r.status}`)
  return (await r.json()) as import('./types.ts').Geometry
}
