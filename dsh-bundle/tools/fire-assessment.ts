/**
 * 命令行：用固定流程和风险检测的代码（src/fire-loop/assessment.ts、src/evidence/check.ts）对 mine_data 跑一次，
 * 输出流程结果和生成的全部记录。一致性测试（mcp/workflow/tests/test_fire_loop_parity.py）用它与 Python 侧逐项比对；也可手工排查。
 *
 *   node tools/fire-assessment.ts <skills 目录> <记录目录> <工作区> <python> <panel> [mine_data URL]
 *   令牌取 RESCUE_MCP_MINE_DATA_TOKEN
 */

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { checkMine } from '../src/evidence/check.ts'
import { McpClient } from '../src/evidence/mcp-client.ts'
import { Evidence } from '../src/evidence/service.ts'
import { runFireAssessment } from '../src/fire-loop/assessment.ts'

const [skillsDir, recordDir, workspace, python, panel, url = process.env.RESCUE_MCP_MINE_DATA_URL ?? 'http://127.0.0.1:8101/mcp'] = process.argv.slice(2)
if (!skillsDir || !recordDir || !workspace || !python || !panel) {
  console.error('用法：node tools/fire-assessment.ts <skills> <records> <workspace> <python> <panel> [URL]')
  process.exit(2)
}
const client = new McpClient({ server: 'mine_data', url, token: process.env.RESCUE_MCP_MINE_DATA_TOKEN })
const ev = new Evidence({ skillsDir, recordDir, python, mineData: (t, a) => client.call(t, a) })
const origin = { channel: 'eval', trigger: 'chat' } as const
const assessment = await runFireAssessment(ev, {
  panel,
  origin,
  chartPath: async (id) => (await ev.exportRecord(id, workspace, 'tool')).files[0] ?? null,
})
const check = await checkMine(ev, { panels: [panel], origin })
const records = Object.fromEntries(
  readdirSync(recordDir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => [f.slice(0, -5), JSON.parse(readFileSync(join(recordDir, f), 'utf8'))]),
)
process.stdout.write(JSON.stringify({ assessment, check, records }) + '\n')
