/**
 * rescue-approval：人工确认节点 + 红线预检（DSH 原生插件）。
 *
 * 挂在 `tools/pre-execute` 上：guard（actions 服务）里按动作分级表属于 C 级（对外动作）的工具在执行前
 *   1. 请求 guard 的 /redlines/check 预检，违规或服务不可用 → deny（附条号）；
 *   2. 其他监听者也放行时 → ask，由 DSH 审批通道请指挥员确认（ctx.approval）。
 * A、B 级工具不弹确认；没有登记的工具按 C 级处理。guard 执行时还会再校验一次，所以即使本插件被误关，违规指令也发不出去。
 *
 * TODO(阶段3): 确认人角色校验。目前任何能看到确认弹窗的人都能确认；
 *   需实现 approval/request 应答者，按动作分级表的 confirmer 校验登录用户角色，并把确认人身份写入审计。
 */

import type { Context, PreToolDecision } from '../adapter/dsh.ts'
import { Schema } from '../adapter/dsh.ts'
import { readFileSync } from 'node:fs'

import { IncidentStore } from '../incident/store.ts'
import { type PendingInfo, recordDecided, recordPending, timeoutOutcome } from './pending.ts'

import { type ActionPolicy, type ApprovalConfig, classify, type FetchLike, precheck } from './policy.ts'

export const name = 'rescue-approval'
export const inject = ['tools']

export type Config = ApprovalConfig

/** 动作分级表：契约导出的 data/action-policy.json（随包发布，src/ 和 lib/ 下相对位置相同） */
export function loadPolicies(file = new URL('../../data/action-policy.json', import.meta.url)): ActionPolicy[] {
  const data = JSON.parse(readFileSync(file, 'utf8')) as { policies?: ActionPolicy[] }
  if (!Array.isArray(data.policies) || data.policies.length === 0) throw new Error('action-policy.json 里没有 policies')
  return data.policies
}

export const Config: Schema<Config> = Schema.object({
  gatedServer: Schema.string().default('actions'),
  policies: Schema.array(Schema.object({
    tool: Schema.string().required(),
    level: Schema.union(['A', 'B', 'C'] as const).required(),
    name: Schema.string().required(),
    confirmer: Schema.string(),
    approval_timeout_min: Schema.number(),
    escalate_to: Schema.string(),
    on_timeout: Schema.union(['approve', 'reject', 'escalate'] as const),
  })).default(loadPolicies()),
  precheckUrl: Schema.string().default('http://127.0.0.1:8106/redlines/check'),
  precheckTimeoutMs: Schema.natural().default(5000),
  token: Schema.string(),
  /** 事件库目录；给了就把人工确认的等待和结果记进事件时间线，监听器据此做批准超时升级 */
  incidentDir: Schema.string(),
  askedTtlMs: Schema.natural().default(3_600_000),
})

export function apply(ctx: Context, config: Config) {
  const fetchImpl = globalThis.fetch as unknown as FetchLike
  const incidents = config.incidentDir ? new IncidentStore(config.incidentDir) : undefined
  /** 弹了确认、还没有结果的调用：callId → 事件和动作信息 */
  const asked = new Map<string, { info: PendingInfo; at: number }>()
  /** pre-execute 返回 ask 之后，确认弹窗不一定会出现（会话取消等），登记就留在表里；每次登记前清掉过期的，表不会无限增长 */
  const sweep = () => {
    const limit = Date.now() - (config.askedTtlMs ?? 3_600_000)
    for (const [k, v] of asked) if (v.at < limit) asked.delete(k)
  }

  // 确认的等待和结果记进事件时间线（失败只记警告，不影响确认）。
  // 第五版：动作分级表 on_timeout 为 approve / reject 的动作，批准超时（真实时间）没人回应就按表自动处理，结果记 auto: timeout；
  // 其余（escalate，缺省）一直等人，由监听器升级提醒，**不会自动执行**。
  ;(ctx as any).on('approval/request', async (req: any, next: () => Promise<string>) => {
    const info = req.callId ? asked.get(String(req.callId))?.info : undefined
    if (!info || !incidents) return next()
    const key = String(req.callId)
    await recordPending(incidents, key, info).catch((err: Error) => ctx.logger.warn(`rescue-approval: 记录待确认失败：${err.message}`))
    let outcome = 'unavailable'
    let auto: 'timeout' | undefined
    const auto_ = timeoutOutcome(info.policy)
    let timer: ReturnType<typeof setTimeout> | undefined
    // 网页端的确认卡片绑在请求的 signal 上，signal 一中止卡片就撤掉：自动处理时要关掉卡片，免得它留在界面上让人误以为还在等
    // （实测：不关的话卡片和会话上的“Approval”标记会一直留着）。换成“原信号 + 我们自己的信号”，到点由我们中止。
    const dismiss = new AbortController()
    if (auto_) {
      try {
        req.signal = req.signal ? AbortSignal.any([req.signal, dismiss.signal]) : dismiss.signal
      } catch {
        // 请求对象不能改：卡片会留到有人点掉，自动处理本身不受影响
      }
    }
    try {
      const answer = next()
      if (auto_) {
        const timedOut = new Promise<'timeout'>((resolve) => {
          timer = setTimeout(() => resolve('timeout'), auto_.ms)
        })
        const first = await Promise.race([answer, timedOut])
        if (first === 'timeout') {
          answer.catch(() => {}) // 弹窗那一侧的结果不要了
          dismiss.abort() // 撤掉网页端的确认卡片
          outcome = auto_.outcome
          auto = 'timeout'
          ctx.logger.info?.(`rescue-approval: ${info.tool} 批准超时（${info.policy?.approval_timeout_min} 分钟），按 on_timeout=${info.policy?.on_timeout} 自动${outcome === 'allowed-once' ? '放行' : '拒绝'}`)
          return outcome as never
        }
        outcome = first
        return outcome as never
      }
      outcome = await answer
      return outcome as never
    } finally {
      if (timer) clearTimeout(timer)
      asked.delete(key)
      await recordDecided(incidents, key, info, outcome, auto).catch((err: Error) => ctx.logger.warn(`rescue-approval: 记录确认结果失败：${err.message}`))
    }
  })

  ctx.on('tools/pre-execute', async (exec, next): Promise<PreToolDecision> => {
    const gate = classify(exec.name, config)
    if (!gate.gated) return next()

    const verdict = await precheck(gate.rawName, exec.arguments, config, exec.signal, fetchImpl)
    if (verdict.kind === 'deny') {
      return { kind: 'deny', reason: verdict.reason, info: { name: 'RescueApproval', code: verdict.code } }
    }
    // 先让其他策略表态；有人拒绝或取消就照办，全部放行才请人确认
    const upstream = await next()
    if (upstream.kind !== 'allow') return upstream
    sweep()
    asked.delete(String(exec.callId)) // callId 被复用时不能沿用上一次的事件编号
    if (verdict.incidentId) asked.set(String(exec.callId), { info: { incidentId: verdict.incidentId, tool: gate.rawName, recordIds: verdict.recordIds ?? [], policy: config.policies.find((p) => p.tool === gate.rawName) }, at: Date.now() })
    return { kind: 'ask', reason: verdict.reason, displayReason: verdict.displayReason }
  })
}
