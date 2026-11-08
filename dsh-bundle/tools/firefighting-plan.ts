/**
 * 命令行：用固定流程 draft_firefighting_plan 的代码（src/fire-loop/sealing.ts）对 mine_data 跑一次，输出流程结果和全部记录。
 * 一致性测试（mcp/contract/tests/test_fire_loop_parity.py）用它与 Python 侧逐项比对。
 *
 * 两个模型阶段用写死的回答代替（第 1 轮审核判“通风稳定”不通过，第 2 轮全部通过，走一遍退回重写）；
 * 红线预检视为通过（actions 的 precheck_plan 由 mcp/actions/tests 测试）。
 *
 *   node tools/firefighting-plan.ts <skills 目录> <记录目录> <panel> [mine_data URL]
 */

import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { McpClient } from '../src/evidence/mcp-client.ts'
import { Evidence } from '../src/evidence/service.ts'
import type { StageRunner } from '../src/fire-loop/explanation.ts'
import { REVIEW_STAGE } from '../src/fire-loop/plan-stages.ts'
import { draftFirefightingPlan } from '../src/fire-loop/sealing.ts'
import { ArticleStore } from '../src/knowledge/articles.ts'

const [skillsDir, recordDir, panel, url = process.env.RESCUE_MCP_MINE_DATA_URL ?? 'http://127.0.0.1:8101/mcp'] = process.argv.slice(2)
if (!skillsDir || !recordDir || !panel) {
  console.error('用法：node tools/firefighting-plan.ts <skills> <records> <panel> [mine_data URL]')
  process.exit(2)
}
const client = new McpClient({ server: 'mine_data', url, token: process.env.RESCUE_MCP_MINE_DATA_TOKEN })
const ev = new Evidence({ skillsDir, recordDir, python: process.env.RESCUE_SKILL_PYTHON ?? 'python3', mineData: (t, a) => client.call(t, a) })
const articles = ArticleStore.load(new URL('../data/article-index.json', import.meta.url).pathname)

let reviews = 0
const stage: StageRunner = async ({ label, schema }) => {
  const props = (schema as any).properties
  if (label === REVIEW_STAGE) {
    reviews++
    const ids: string[] = props.items.items.properties.id.enum
    const items = ids.map((id) => ({ id, conclusion: reviews === 1 && id === 'ventilation' ? 'fail' : 'pass', articles: id === 'ventilation' ? ['第98条'] : [], note: reviews === 1 && id === 'ventilation' ? '密闭位置需写明现场侦察确认' : '' }))
    return { ok: true, structured: { items, summary: reviews === 1 ? '补充通风稳定说明后重审' : '按必备步骤注惰并监测' }, childId: `cli-review-${reviews}` }
  }
  return {
    ok: true,
    structured: { optional_steps: ['leak_sealing', 'monitor_after'], step_notes: [{ key: 'inert_injection', note: '注氮口位置按现场确定' }], summary: '先注惰降氧，辅以回风侧堵漏，不封闭火区' },
    childId: 'cli-write',
  }
}

const out = await draftFirefightingPlan(ev, {
  panel,
  origin: { channel: 'eval', trigger: 'chat' },
  stage,
  precheck: async () => ({ ok: true, problems: [], notes: [] }),
  getArticles: (document, list) => articles.get(document, list),
})
const records = Object.fromEntries(
  readdirSync(recordDir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => [f.slice(0, -5), JSON.parse(readFileSync(join(recordDir, f), 'utf8'))]),
)
process.stdout.write(JSON.stringify({ plan: out, records }) + '\n')
