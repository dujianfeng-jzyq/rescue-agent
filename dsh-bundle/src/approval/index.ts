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
  })).default(loadPolicies()),
  precheckUrl: Schema.string().default('http://127.0.0.1:8106/redlines/check'),
  precheckTimeoutMs: Schema.natural().default(5000),
  token: Schema.string(),
})

export function apply(ctx: Context, config: Config) {
  const fetchImpl = globalThis.fetch as unknown as FetchLike

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
    return { kind: 'ask', reason: verdict.reason, displayReason: verdict.displayReason }
  })
}
