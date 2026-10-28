/**
 * rescue-audit：把 actions 指令类工具的最终结果（已发布 / 被拦截 / 失败）写入指令台账（JSONL，只追加）。
 *
 * TODO(阶段3): 复盘报告生成（从会话日志 + 指令台账 + actions 服务台账汇总时间线、决策点和偏差）。
 */

import { appendFile, mkdir } from 'node:fs/promises'
import { dirname } from 'node:path'

import type { Context } from '../adapter/dsh.ts'
import { Schema } from '../adapter/dsh.ts'
import { classify } from '../approval/policy.ts'
import { toLine, toRecord } from './record.ts'

export const name = 'rescue-audit'
export const inject = ['tools']

export interface Config {
  path: string
  gatedServer: string
  exemptPrefixes: string[]
}

export const Config: Schema<Config> = Schema.object({
  path: Schema.string().default('var/audit/tool-actions.jsonl'),
  gatedServer: Schema.string().default('actions'),
  exemptPrefixes: Schema.array(Schema.string()).default([]),
})

export function apply(ctx: Context, config: Config) {
  let ready: Promise<unknown> = mkdir(dirname(config.path), { recursive: true })

  ctx.on('tools/result', (exec, result) => {
    if (!classify(exec.name, config).gated) return
    const record = toRecord({
      callId: String(exec.callId),
      name: exec.name,
      sessionId: exec.agent ? String(exec.agent.id) : undefined,
      arguments: exec.arguments,
      isError: result.isError,
      errorMessage: result.isError ? result.error.message : undefined,
      errorCode: result.isError ? result.error.info?.code : undefined,
    })
    // 顺序写入；写失败只记日志，不影响工具结果（会话日志仍是权威记录）
    ready = ready
      .then(() => appendFile(config.path, toLine(record), 'utf8'))
      .catch(err => ctx.logger.warn(`rescue-audit: 写指令台账失败：${(err as Error).message}`))
    return undefined
  })
}
