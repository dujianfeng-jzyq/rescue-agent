/**
 * rescue-map：三维地图（docs/design-v4-map.md）的 Node 侧。
 *
 * 宿主只把包根名称的 Loader 入口当客户端包扫描（验证清单 #25），所以本插件是 `@rescue/dsh-bundle` 的根入口；
 * 浏览器侧是同一个包的 `./client`（client/src，由 package.json 的 dsh.client 声明）。
 *
 * 提供三个路由（都校验浏览器 cookie，与 /api 同一套信任规则）：
 *   GET  /rescue-map/geometry.json  底图几何（地图包目录里的 geometry.json，scripts/map/extract_geometry.py 生成）；没有这个文件时返回 404，浏览器端退回 DSH 原界面
 *   GET  /rescue-map/state          大屏状态汇总（事件库、记录、监听器心跳、矿井读数），浏览器每几秒轮询
 *   GET  /rescue-map/events         SSE：先发快照，之后推送地图指令
 *   POST /rescue-map/emit           调试用（config.debug），发一条地图指令
 */

import { readFileSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { join } from 'node:path'

import type { Context } from '../adapter/dsh.ts'
import { Schema } from '../adapter/dsh.ts'
import { McpClient } from '../evidence/mcp-client.ts'
import { getHub } from './hub.ts'
import type { MapCommand } from './hub.ts'
import { getActivity } from './activity.ts'
import { Autopilot } from './autopilot.ts'
import { MineMapLoader } from './mapping.ts'
import { geometryInfo } from './names.ts'
import type { Network } from './evacuation.ts'
import { buildState } from './state.ts'
import type { DemoStatus } from './state.ts'
import type { MapState } from './state.ts'

export const name = 'rescue-map'
export const inject = ['webServer', 'connection']

export interface Config {
  /** 地图包目录：里面的 geometry.json 是当前矿井的底图几何。真实矿井图不进仓库，放在被忽略的 var/map/（容器里只读挂载到 /data/map） */
  dataDir: string
  /** 开启调试路由 POST /rescue-map/emit */
  debug: boolean
  /** 状态汇总的数据来源：事件库、记录目录、矿井数据服务（缺省没有就不显示读数和矿井时间） */
  incidentDir: string
  recordDir: string
  mineDataUrl?: string | undefined
  mineDataToken?: string | undefined
  /** 左栏读数显示的工作面（缺省取当前事件的工作面） */
  panel?: string | undefined
  /** 演示进度（模拟矿井的 /demo/status，用 mineDataToken）：演示结束时大屏显示“演示已结束”。只在演示环境配置 */
  demoStatusUrl?: string | undefined
  /** 自动叠加：按状态和映射表把等级、报警、火区画到图上，来了新告警自动飞过去（不经模型） */
  autopilot: boolean
}

export const Config: Schema<Config> = Schema.object({
  dataDir: Schema.string().required(),
  debug: Schema.boolean().default(false),
  incidentDir: Schema.string().required(),
  recordDir: Schema.string().required(),
  mineDataUrl: Schema.string(),
  mineDataToken: Schema.string(),
  panel: Schema.string(),
  demoStatusUrl: Schema.string(),
  autopilot: Schema.boolean().default(true),
})

/** 进程内唯一的指令中心，工具和固定流程经它发地图指令。 */
export const hub = getHub()

const HEARTBEAT_MS = 15_000

function readBody(req: IncomingMessage, limit = 16 * 1024): Promise<string> {
  return new Promise((resolve, reject) => {
    let n = 0
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => {
      n += c.length
      if (n > limit) {
        reject(new Error('请求体过大'))
        req.destroy()
      } else chunks.push(c)
    })
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

export function apply(ctx: Context, config: Config): void {
  const c = ctx as any
  const dataDir = config.dataDir

  /** 校验浏览器请求；不通过时已经写了响应，返回 false。 */
  function admitted(req: IncomingMessage, res: ServerResponse): boolean {
    let rejection: number | undefined = c.connection.requestRejection(req)
    if (rejection === undefined) {
      const admission = c.connection.admit(req)
      if ('rejection' in admission) rejection = admission.rejection as number
    }
    if (rejection === undefined) return true
    res.writeHead(rejection, { 'content-type': 'text/plain; charset=utf-8' })
    res.end(rejection === 401 ? '未登录' : '被拒绝')
    return false
  }

  // 智能体的实时活动：旁路观察 DSH 的事件（忙/闲、正在调什么工具、等人确认、等人回答），一律原样放行，观察出错也不影响它们
  const act = getActivity()
  const safely = (f: () => void): void => {
    try {
      f()
    } catch (e) {
      ctx.logger.debug?.(`rescue-map: 记录智能体活动失败：${(e as Error).message}`)
    }
  }
  const aid = (a: any): string => String(a?.id ?? a?.session?.id ?? 'unknown')
  c.on('agent/status', (p: any) => safely(() => act.status(aid(p.agent), p.status)))
  c.on('agent/disposed', (p: any) => safely(() => act.dispose(aid(p.agent))))
  c.on('tools/pre-execute', async (exec: any, next: () => Promise<unknown>) => {
    safely(() => act.toolStart(String(exec.callId), aid(exec.agent), String(exec.name)))
    return next()
  })
  c.on('tools/post-execute', async (exec: any, _result: unknown, next: () => Promise<unknown>) => {
    safely(() => act.toolEnd(String(exec.callId), aid(exec.agent)))
    return next()
  })
  c.on('approval/request', async (req: any, next: () => Promise<unknown>) => {
    const id = String(req.callId ?? `approval-${Date.now()}`)
    safely(() => act.approvalStart(id, String(req.toolName)))
    try {
      return await next()
    } finally {
      safely(() => act.approvalEnd(id))
    }
  })
  c.on('user-questions/request', async (req: any, next: () => Promise<unknown>) => {
    const id = String(req.callId ?? `question-${Date.now()}`)
    safely(() => act.questionStart(id))
    try {
      return await next()
    } finally {
      safely(() => act.questionEnd(id))
    }
  })

  const disposers: Array<() => void> = []
  const route = (path: string, handler: (req: IncomingMessage, res: ServerResponse) => void | Promise<void>): void => {
    disposers.push(c.webServer.register({ kind: 'exact', path, handler }))
  }

  route('/rescue-map/geometry.json', (req, res) => {
    if (!admitted(req, res)) return
    try {
      const body = readFileSync(join(dataDir, 'geometry.json'))
      res.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-cache' })
      res.end(body)
    } catch {
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' })
      res.end('没有地图几何：把 geometry.json 放进地图包目录（scripts/map/extract_geometry.py 生成）')
    }
  })

  // 映射表（地图包里的 mine_map.yaml）：名称必须在几何数据的 names 里，否则丢弃并警告
  const mapLoader = new MineMapLoader(dataDir, geometryInfo(dataDir))
  let lastWarnings = ''

  // 状态汇总：2 秒缓存，多个网页端同时轮询、自动叠加的定时器都共用，只查一次矿井
  const mine = config.mineDataUrl ? new McpClient({ server: 'mine_data', url: config.mineDataUrl, token: config.mineDataToken, timeoutMs: 4000 }) : null
  // 矿井网络（撤人路线用）：几乎不变，缓存 60 秒；取不到不影响其他部分
  let netCache: { at: number; value: Promise<Network | null> } | null = null
  const getNetwork = (): Promise<Network | null> => {
    if (!mine) return Promise.resolve(null)
    if (!netCache || Date.now() - netCache.at > 60_000) {
      netCache = { at: Date.now(), value: mine.call('get_network', {}).then((n: any) => (n && Array.isArray(n.nodes) && Array.isArray(n.roadways) ? (n as Network) : null)).catch(() => null) }
    }
    return netCache.value
  }
  const demoStatus = config.demoStatusUrl
    ? async (): Promise<DemoStatus | null> => {
        const r = await fetch(config.demoStatusUrl!, { headers: config.mineDataToken ? { authorization: `Bearer ${config.mineDataToken}` } : {}, signal: AbortSignal.timeout(3000) })
        if (!r.ok) return null
        const j: any = await r.json()
        return { state: String(j.state ?? ''), outcome: String(j.outcome ?? ''), endedAt: j.ended_at ? String(j.ended_at) : null }
      }
    : undefined
  let cache: { at: number; value: Promise<MapState> } | null = null
  const getState = (): Promise<MapState> => {
    if (!cache || Date.now() - cache.at > 2000) {
      const { map, warnings } = mapLoader.get()
      const joined = warnings.join('\n')
      if (joined !== lastWarnings) {
        lastWarnings = joined
        for (const w of warnings) ctx.logger.warn(`rescue-map: mine_map.yaml：${w}`)
      }
      cache = { at: Date.now(), value: buildState({ incidentDir: config.incidentDir, recordDir: config.recordDir, mine, panel: config.panel, map, activity: getActivity().snapshot(), network: getNetwork, demoStatus }).then((s) => ({ ...s, mapWarnings: warnings })) }
    }
    return cache.value
  }
  route('/rescue-map/state', async (req, res) => {
    if (!admitted(req, res)) return
    try {
      const body = JSON.stringify(await getState())
      res.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
      res.end(body)
    } catch (e) {
      cache = null
      res.writeHead(500, { 'content-type': 'application/json; charset=utf-8' })
      res.end(JSON.stringify({ error: String((e as Error).message) }))
    }
  })

  // 自动叠加：不依赖有没有网页端连着，标记和镜头指令进指令中心，新连接的网页端先收到快照
  if (config.autopilot) {
    const pilot = new Autopilot()
    let running = false
    const timer = setInterval(() => {
      if (running) return
      running = true
      void (async () => {
        try {
          const st = await getState()
          for (const cmd of pilot.step(st, mapLoader.get().map, hub.snapshot().marks, hub.snapshot().figures)) {
            try {
              hub.emit(cmd)
            } catch (e) {
              ctx.logger.warn(`rescue-map: 自动叠加的指令被拒绝：${(e as Error).message}`)
            }
          }
        } catch (e) {
          ctx.logger.debug?.(`rescue-map: 自动叠加这一轮失败：${(e as Error).message}`)
        } finally {
          running = false
        }
      })()
    }, 3000)
    disposers.push(() => clearInterval(timer))
  }

  route('/rescue-map/events', (req, res) => {
    if (!admitted(req, res)) return
    res.writeHead(200, {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-store',
      connection: 'keep-alive',
      'x-accel-buffering': 'no',
    })
    const unsubscribe = hub.subscribe((chunk) => res.write(chunk))
    const beat = setInterval(() => res.write(': ping\n\n'), HEARTBEAT_MS)
    const done = (): void => {
      clearInterval(beat)
      unsubscribe()
    }
    req.on('close', done)
    res.on('error', done)
  })

  if (config.debug) {
    route('/rescue-map/emit', async (req, res) => {
      if (!admitted(req, res)) return
      if (req.method !== 'POST') {
        res.writeHead(405).end()
        return
      }
      try {
        const cmd = JSON.parse(await readBody(req)) as MapCommand
        const event = hub.emit(cmd)
        res.writeHead(200, { 'content-type': 'application/json; charset=utf-8' })
        res.end(JSON.stringify({ ...event, clients: hub.clientCount }))
      } catch (e) {
        res.writeHead(400, { 'content-type': 'application/json; charset=utf-8' })
        res.end(JSON.stringify({ error: String((e as Error).message) }))
      }
    })
  }

  c.on('dispose', () => disposers.forEach((d) => d()))
}
