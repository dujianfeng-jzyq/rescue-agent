/**
 * rescue-wake：值班会话唤醒插件（第三版 P3，docs/design-v3.md 7.3，验证清单 #24）。
 *
 * 全局一份（不在预设里）：每 pollMs 扫描事件库（监听器写的），把未投递的告警投递到值班会话：
 * 取值班会话（编号记在事件库目录的 duty-session.json；没有或恢复失败就新建）→ resolveAgent → agent.followup(消息) → flush 落盘。
 * 没有任何网页端连接也能工作；操作员事后打开网页端，值班会话里已有智能体的研判。
 * 唤醒失败时，告警仍是“未确认”，控制台继续显示（design-v3 7.3 退路）。
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { Context } from '../adapter/dsh.ts'
import { loadCreateUserMessage, Schema } from '../adapter/dsh.ts'
import { IncidentStore } from './store.ts'
import { Waker } from './wake.ts'

export const name = 'rescue-wake'
export const inject = ['sessionController', 'sessions']

export interface Config {
  incidentDir: string
  /** 值班会话的工作目录 */
  cwd: string
  agentPreset: string
  /** 值班会话所属的工作区（网页端侧栏分组）；缺省不指定 */
  workspaceId?: string
  pollMs: number
  /** 投递失败后的重试退避（秒）；用完放弃 */
  backoffS: number[]
}

export const Config: Schema<Config> = Schema.object({
  incidentDir: Schema.string().required(),
  cwd: Schema.string().required(),
  agentPreset: Schema.string().default('rescue-commander'),
  workspaceId: Schema.string(),
  pollMs: Schema.number().default(2000),
  backoffS: Schema.array(Schema.number()).default([30, 120, 600]),
})

export function apply(ctx: Context, config: Config): void {
  const c = ctx as any
  const store = new IncidentStore(config.incidentDir)
  const dutyFile = join(config.incidentDir, 'duty-session.json')
  mkdirSync(config.incidentDir, { recursive: true })
  mkdirSync(config.cwd, { recursive: true })
  let stopped = false

  async function dutyAgent(): Promise<{ agent: any; sessionId: string }> {
    if (existsSync(dutyFile)) {
      const saved = JSON.parse(readFileSync(dutyFile, 'utf8')) as { sessionId: string }
      const resolved = await c.sessionController.resolveAgent(saved.sessionId)
      if (!('error' in resolved)) return { agent: resolved.agent, sessionId: saved.sessionId }
      ctx.logger.warn(`rescue-wake: 值班会话 ${saved.sessionId} 恢复失败，新建：${String(resolved.error?.message ?? resolved.error)}`)
    }
    const made = await c.sessionController.create({ cwd: config.cwd, agentPreset: config.agentPreset, ...(config.workspaceId ? { workspaceId: config.workspaceId } : {}) })
    const sessionId: string = made.sessionId
    const resolved = await c.sessionController.resolveAgent(sessionId)
    if ('error' in resolved) throw resolved.error
    writeFileSync(dutyFile, JSON.stringify({ sessionId, createdAt: new Date().toISOString() }))
    return { agent: resolved.agent, sessionId }
  }

  const waker = new Waker(store, {
    backoffS: config.backoffS,
    log: (m) => ctx.logger.warn(`rescue-wake: ${m}`),
    deliver: async (text) => {
      const createUserMessage = await loadCreateUserMessage()
      const { agent, sessionId } = await dutyAgent()
      // 消息来源 kind 不能是 plugin（v4 会话格式拒绝）；用 rescue-sentinel
      agent.followup(createUserMessage({ content: [{ type: 'text', text }], source: { kind: 'rescue-sentinel' } }))
      const flushed = await c.sessions.flush(agent.session)
      if (!flushed) throw new Error('会话落盘失败')
      return { sessionId }
    },
  })

  const timer = setInterval(() => {
    if (!stopped) void waker.drain().catch((err) => ctx.logger.warn(`rescue-wake: ${(err as Error).message}`))
  }, config.pollMs)
  c.on('dispose', () => {
    stopped = true
    clearInterval(timer)
  })
}
