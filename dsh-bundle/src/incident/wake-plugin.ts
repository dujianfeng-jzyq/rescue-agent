/**
 * rescue-wake：值班会话唤醒插件（第三版 P3，docs/design-v3.md 7.3，验证清单 #24）。
 *
 * 全局一份（不在预设里）：每 pollMs 扫描事件库（监听器写的），把未投递的告警投递到值班会话：
 * 取值班会话（编号记在事件库目录的 duty-session.json；没有或恢复失败就新建）→ resolveAgent → agent.followup(消息) → flush 落盘。
 * 没有任何网页端连接也能工作；操作员事后打开网页端，值班会话里已有智能体的研判。
 * 值班会话正忙时不投递（见 wake.ts 文件头）：等它空闲再按时间线判断，已被处理覆盖的告警不再投递。
 * 每次轮询把值班会话忙不忙写到事件库目录的 duty-status.json（{busy, wall}）：演示导演据此等智能体处理完再推进矿井时间。
 * 唤醒失败时，告警仍是“未确认”，控制台继续显示（design-v3 7.3 退路）。
 */

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import type { Context } from '../adapter/dsh.ts'
import { loadCreateUserMessage, Schema } from '../adapter/dsh.ts'
import { IncidentStore } from './store.ts'
import { Waker } from './wake.ts'

export const name = 'rescue-wake'
export const inject = ['sessionController', 'sessions', 'workspaceController']

export interface Config {
  incidentDir: string
  /** 值班会话的工作目录 */
  cwd: string
  agentPreset: string
  /** 值班会话的标题：创建后显式设置（DSH 的显式标题会“固定”住，之后不再自动生成），操作员在侧栏里按它找到值班会话 */
  title: string
  /** 值班会话所属的工作区 id；缺省在 cwd 上建（或复用）一个工作区，否则网页端侧栏把它放进折叠的 “Ungrouped”（验证清单 #24） */
  workspaceId?: string
  /** 自动建的工作区的标题 */
  workspaceTitle: string
  pollMs: number
  /** 投递失败后的重试退避（秒）；用完放弃 */
  backoffS: number[]
}

export const Config: Schema<Config> = Schema.object({
  incidentDir: Schema.string().required(),
  cwd: Schema.string().required(),
  agentPreset: Schema.string().default('rescue-commander'),
  title: Schema.string().default('值班会话（监听器告警）'),
  workspaceId: Schema.string(),
  workspaceTitle: Schema.string().default('值班'),
  pollMs: Schema.number().default(2000),
  backoffS: Schema.array(Schema.number()).default([30, 120, 600]),
})

export function apply(ctx: Context, config: Config): void {
  const c = ctx as any
  const store = new IncidentStore(config.incidentDir)
  const dutyFile = join(config.incidentDir, 'duty-session.json')
  const statusFile = join(config.incidentDir, 'duty-status.json')
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
    let workspaceId = config.workspaceId
    if (!workspaceId) {
      try {
        // 在值班工作目录上建工作区（已有就复用）；新建的命名为“值班”。失败时退回只给 cwd（会话进 Ungrouped，不影响投递）
        const made = await c.workspaceController.create({ path: config.cwd })
        workspaceId = String(made.workspace.workspaceId)
        if (made.created) await c.workspaceController.rename({ workspaceId, title: config.workspaceTitle }).catch((err: Error) => ctx.logger.warn(`rescue-wake: 工作区命名失败：${err.message}`))
      } catch (err) {
        ctx.logger.warn(`rescue-wake: 建值班工作区失败，会话将在 Ungrouped：${(err as Error).message}`)
      }
    }
    const made = await c.sessionController.create({ ...(workspaceId ? { workspaceId } : { cwd: config.cwd }), agentPreset: config.agentPreset })
    const sessionId: string = made.sessionId
    const resolved = await c.sessionController.resolveAgent(sessionId)
    if ('error' in resolved) throw resolved.error
    // 先命名再投递：显式标题固定后，DSH 不会再用首条消息或模型生成标题。命名失败不影响告警投递
    try {
      await c.sessionController.rename({ sessionId, title: config.title })
    } catch (err) {
      ctx.logger.warn(`rescue-wake: 值班会话命名失败：${(err as Error).message}`)
    }
    writeFileSync(dutyFile, JSON.stringify({ sessionId, createdAt: new Date().toISOString() }))
    return { agent: resolved.agent, sessionId }
  }

  /** 值班会话正忙：正在跑（status 为 running，含等人工确认、回答卡片），或队列里还有没处理的消息。没有值班会话或恢复失败按不忙（投递时新建） */
  async function dutyBusy(): Promise<boolean> {
    if (!existsSync(dutyFile)) return false
    const saved = JSON.parse(readFileSync(dutyFile, 'utf8')) as { sessionId: string }
    const resolved = await c.sessionController.resolveAgent(saved.sessionId)
    if ('error' in resolved) return false
    const agent = resolved.agent
    return agent.status !== 'idle' || (agent.inbox?.nextTurn?.length ?? 0) > 0 || (agent.inbox?.nextStep?.length ?? 0) > 0
  }

  const waker = new Waker(store, {
    backoffS: config.backoffS,
    busy: dutyBusy,
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

  /** 写值班会话状态（演示导演读）；查不到状态时不写，导演看文件过期就不等 */
  async function writeStatus(): Promise<void> {
    const busy = await dutyBusy()
    const tmp = `${statusFile}.tmp`
    writeFileSync(tmp, JSON.stringify({ busy, wall: Date.now() / 1000 }))
    renameSync(tmp, statusFile)
  }

  const timer = setInterval(() => {
    if (stopped) return
    const warn = (err: unknown) => ctx.logger.warn(`rescue-wake: ${(err as Error).message}`)
    void waker.drain().catch(warn).then(writeStatus).catch(warn)
  }, config.pollMs)
  c.on('dispose', () => {
    stopped = true
    clearInterval(timer)
  })
}
