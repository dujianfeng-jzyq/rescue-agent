/**
 * 命令行：用固定流程 review_effect 的代码（src/fire-loop/effect.ts）对 mine_data 跑一次，输出流程结果和全部记录。
 * 一致性测试（mcp/contract/tests/test_fire_loop_parity.py）用它与 Python 侧逐项比对。since 不给时视为台账里没有下发记录。
 *
 *   node tools/effect.ts <skills 目录> <记录目录> <panel> <mine_data URL> [since]
 */

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { McpClient } from '../src/evidence/mcp-client.ts'
import { Evidence } from '../src/evidence/service.ts'
import { reviewEffect } from '../src/fire-loop/effect.ts'

const [skillsDir, recordDir, panel, url, since] = process.argv.slice(2)
if (!skillsDir || !recordDir || !panel || !url) {
  console.error('用法：node tools/effect.ts <skills> <records> <panel> <URL> [since]')
  process.exit(2)
}
const client = new McpClient({ server: 'mine_data', url, token: process.env.RESCUE_MCP_MINE_DATA_TOKEN })
const ev = new Evidence({ skillsDir, recordDir, python: process.env.RESCUE_SKILL_PYTHON ?? 'python3', mineData: (t, a) => client.call(t, a) })
const out = await reviewEffect(ev, { panel, ...(since ? { since } : {}), origin: { channel: 'eval', trigger: 'chat' }, lastIssued: async () => ({ found: false }) })
const records = Object.fromEntries(
  readdirSync(recordDir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => [f.slice(0, -5), JSON.parse(readFileSync(join(recordDir, f), 'utf8'))]),
)
process.stdout.write(JSON.stringify({ effect: out, records }) + '\n')
