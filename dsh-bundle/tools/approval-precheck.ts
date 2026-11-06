/**
 * 命令行：用审批插件的真实逻辑（classify + precheck，读 data/action-policy.json）对一个 guard 预检一条指令。
 * 三道防线的集成测试（mcp/actions/tests/test_three_lines.py）用它，不经 DSH：
 *   node tools/approval-precheck.ts <guard 预检地址> <工具名> <参数 JSON 文件> [令牌]
 * 输出一行 JSON：{ gated, verdict }。工具名写 guard 的原始名（不带 mcp__actions__ 前缀）。
 */

import { readFileSync } from 'node:fs'

import { loadPolicies } from '../src/approval/index.ts'
import { type ApprovalConfig, classify, type FetchLike, precheck } from '../src/approval/policy.ts'

const [url, tool, argsFile, token] = process.argv.slice(2)
if (!url || !tool || !argsFile) {
  console.error('用法：node tools/approval-precheck.ts <guard 预检地址> <工具名> <参数 JSON 文件> [令牌]')
  process.exit(2)
}

const config: ApprovalConfig = { gatedServer: 'actions', policies: loadPolicies(), precheckUrl: url, precheckTimeoutMs: 5000, token }
const gate = classify(`mcp__actions__${tool}`, config)
if (!gate.gated) {
  console.log(JSON.stringify({ gated: false }))
} else {
  const verdict = await precheck(gate.rawName, JSON.parse(readFileSync(argsFile, 'utf8')), config, new AbortController().signal, globalThis.fetch as unknown as FetchLike)
  console.log(JSON.stringify({ gated: true, verdict }))
}
