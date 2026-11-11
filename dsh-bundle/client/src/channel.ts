import type { MapCommand, MapSnapshot } from './types.ts'

export interface Channel {
  close(): void
}

/** 服务端每 15 秒发一次带序号的心跳；超过这么久什么都没收到，就认为连接卡住了 */
const SILENT_MS = 40_000
const RETRY_MS = [2_000, 5_000, 10_000, 20_000]

/**
 * 订阅 Node 侧的地图指令：先收到快照，之后逐条指令（带序号）。
 * 浏览器的 EventSource 只在网络断开时自动重连：服务端回了非 200（如当时被限流）它就永久放弃；连接卡住不报错它也察觉不到。
 * 所以这里自己兜底（2026-11-10 演示：地图标记停在蓝色，左栏已经红色）：
 *   - 连接被放弃（readyState CLOSED）→ 退避后重建；
 *   - 超过 SILENT_MS 没收到任何事件（含心跳）→ 重建；
 *   - 心跳或指令的序号对不上（漏收了指令）→ 重建。
 * 每次重建都会先收到完整快照，标记和图元直接对齐。
 */
export function connect(
  onSnapshot: (s: MapSnapshot) => void,
  onCommand: (c: MapCommand) => void,
  onState: (connected: boolean) => void,
): Channel {
  let es: EventSource | null = null
  let seq = -1
  let lastMsg = Date.now()
  let failures = 0
  let retry = 0
  let closed = false

  const reopen = (delay: number): void => {
    es?.close()
    es = null
    window.clearTimeout(retry)
    if (!closed) retry = window.setTimeout(open, delay)
  }

  function open(): void {
    if (closed) return
    lastMsg = Date.now()
    seq = -1
    const src = new EventSource(new URL('rescue-map/events', document.baseURI), { withCredentials: true })
    es = src
    src.addEventListener('open', () => {
      failures = 0
      onState(true)
    })
    src.addEventListener('error', () => {
      onState(false)
      if (src.readyState === EventSource.CLOSED && es === src) reopen(RETRY_MS[Math.min(failures++, RETRY_MS.length - 1)]!)
    })
    src.addEventListener('snapshot', (e) => {
      lastMsg = Date.now()
      const s = JSON.parse((e as MessageEvent<string>).data) as MapSnapshot
      seq = s.seq
      onSnapshot(s)
    })
    src.addEventListener('cmd', (e) => {
      lastMsg = Date.now()
      const ev = JSON.parse((e as MessageEvent<string>).data) as { seq?: number; cmd: MapCommand }
      if (typeof ev.seq === 'number' && seq >= 0 && ev.seq !== seq + 1) {
        reopen(0) // 中间漏了指令：重新取快照
        return
      }
      if (typeof ev.seq === 'number') seq = ev.seq
      onCommand(ev.cmd)
    })
    src.addEventListener('ping', (e) => {
      lastMsg = Date.now()
      const p = JSON.parse((e as MessageEvent<string>).data) as { seq?: number }
      if (typeof p.seq === 'number' && seq >= 0 && p.seq !== seq) reopen(0)
    })
  }

  const watchdog = window.setInterval(() => {
    if (!closed && es && Date.now() - lastMsg > SILENT_MS) {
      onState(false)
      reopen(0)
    }
  }, 5_000)

  open()
  return {
    close: () => {
      closed = true
      window.clearInterval(watchdog)
      window.clearTimeout(retry)
      es?.close()
    },
  }
}

export async function loadGeometry(): Promise<import('./types.ts').Geometry> {
  const r = await fetch(new URL('rescue-map/geometry.json', document.baseURI), { credentials: 'same-origin' })
  if (!r.ok) throw new Error(`几何数据加载失败：${r.status}`)
  return (await r.json()) as import('./types.ts').Geometry
}
