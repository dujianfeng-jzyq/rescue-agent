/**
 * 命令行：持续检测的期望测试（design-v2 第 9 节 P4 验收，不经模型）。对手动时钟的模拟矿井：基线检测 → 每次推进一个检测周期
 * （watch.yaml 的 interval_min）→ tick()，直到结束或推进满 hours 小时。输出结束原因、结束时的矿井时刻和结果。
 * mcp/contract/tests/test_watch_expectations.py 按 eval/expectations.yaml 的 watch 期望判定。
 *
 *   node tools/watch.ts <skills 目录> <记录目录> <panel> <mine_data URL> <sim 控制 URL> <hours>
 */

import { McpClient } from '../src/evidence/mcp-client.ts'
import { Evidence } from '../src/evidence/service.ts'
import { Watcher, watchParams } from '../src/evidence/watch.ts'

const [skillsDir, recordDir, panel, url, simUrl, hours] = process.argv.slice(2)
if (!skillsDir || !recordDir || !panel || !url || !simUrl || !hours) {
  console.error('用法：node tools/watch.ts <skills> <records> <panel> <mine_data URL> <sim URL> <hours>')
  process.exit(2)
}
const client = new McpClient({ server: 'mine_data', url, token: process.env.RESCUE_MCP_MINE_DATA_TOKEN })
const ev = new Evidence({ skillsDir, recordDir, python: process.env.RESCUE_SKILL_PYTHON ?? 'python3', mineData: (t, a) => client.call(t, a) })
const params = await watchParams(skillsDir)
const w = new Watcher(ev, { panels: [panel], origin: { channel: 'eval', trigger: 'watch' }, params })
const alarmed = await w.start()
let end = alarmed.length ? ({ reason: 'anomaly', anomaly: true } as const) : null
let advanced = 0
while (!end && advanced < Number(hours) * 60) {
  const r = await fetch(`${simUrl}/advance`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ hours: params.interval_min / 60 }) })
  if (!r.ok) throw new Error(`推进模拟时钟失败：HTTP ${r.status}`)
  advanced += params.interval_min
  end = await w.tick()
}
const result = await w.finish(end ?? { reason: 'expired', anomaly: false })
process.stdout.write(JSON.stringify({ ended: Boolean(end), advanced_h: advanced / 60, result }) + '\n')
