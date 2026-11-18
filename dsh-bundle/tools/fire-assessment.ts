/**
 * 命令行：用固定流程和风险检测的代码（src/fire-loop/assessment.ts、src/evidence/check.ts）对 mine_data 跑一次，
 * 输出流程结果和生成的全部记录。一致性测试（mcp/workflow/tests/test_fire_loop_parity.py）用它与 Python 侧逐项比对；也可手工排查。
 *
 *   node tools/fire-assessment.ts <skills 目录> <记录目录> <工作区> <python> <panel> [mine_data URL]
 *   令牌取 RESCUE_MCP_MINE_DATA_TOKEN；设了 RESCUE_MCP_ROUTE_PLANNER_URL 时请求避灾路线第二意见（第六版），
 *   设了 RESCUE_MCP_FIRE_SIM_URL 时请求火灾蔓延第二意见（第七版）
 */

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { checkMine } from '../src/evidence/check.ts'
import { McpClient } from '../src/evidence/mcp-client.ts'
import { Evidence } from '../src/evidence/service.ts'
import { runFireAssessment } from '../src/fire-loop/assessment.ts'
import { ArticleStore } from '../src/knowledge/articles.ts'

const [skillsDir, recordDir, workspace, python, panel, url = process.env.RESCUE_MCP_MINE_DATA_URL ?? 'http://127.0.0.1:8101/mcp'] = process.argv.slice(2)
if (!skillsDir || !recordDir || !workspace || !python || !panel) {
  console.error('用法：node tools/fire-assessment.ts <skills> <records> <workspace> <python> <panel> [URL]')
  process.exit(2)
}
const client = new McpClient({ server: 'mine_data', url, token: process.env.RESCUE_MCP_MINE_DATA_TOKEN })
const plannerUrl = process.env.RESCUE_MCP_ROUTE_PLANNER_URL
const planner = plannerUrl ? new McpClient({ server: 'route_planner', url: plannerUrl, token: process.env.RESCUE_MCP_ROUTE_PLANNER_TOKEN, timeoutMs: 10_000 }) : undefined
const fireSimUrl = process.env.RESCUE_MCP_FIRE_SIM_URL
const fireSim = fireSimUrl ? new McpClient({ server: 'fire_sim', url: fireSimUrl, token: process.env.RESCUE_MCP_FIRE_SIM_TOKEN, timeoutMs: 90_000 }) : undefined
const ev = new Evidence({
  skillsDir,
  recordDir,
  python,
  mineData: (t, a) => client.call(t, a),
  ...(planner ? { routePlanner: (t: string, a: Record<string, unknown>, s?: AbortSignal) => planner.call(t, a, s) } : {}),
  ...(fireSim ? { fireSim: (t: string, a: Record<string, unknown>, s?: AbortSignal) => fireSim.call(t, a, s) } : {}),
})
const origin = { channel: 'eval', trigger: 'chat' } as const
const articles = ArticleStore.load(new URL('../data/article-index.json', import.meta.url).pathname)
const assessment = await runFireAssessment(ev, {
  panel,
  origin,
  chartPath: async (id) => (await ev.exportRecord(id, workspace, 'tool')).files[0] ?? null,
  getArticles: (document, list) => articles.get(document, list),
})
const check = await checkMine(ev, { panels: [panel], origin })
const records = Object.fromEntries(
  readdirSync(recordDir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => [f.slice(0, -5), JSON.parse(readFileSync(join(recordDir, f), 'utf8'))]),
)
process.stdout.write(JSON.stringify({ assessment, check, records }) + '\n')
