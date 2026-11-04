/**
 * 命令行：用证据插件的采集代码（src/evidence/collect.ts）对 mine_data 采集一次，输出规范化 JSON 和 content_sha256。
 * 一致性测试（mcp/contract/tests/test_collect_parity.py）用它与 Python 采集小工具比对；也可手工排查。
 *
 *   node tools/collect.ts <collect.yaml> <kind> <panel> [mine_data URL] [since]
 *   令牌取 RESCUE_MCP_MINE_DATA_TOKEN
 */

import { readFileSync } from 'node:fs'

import { canonicalJson, contentSha256 } from '../src/evidence/canonical.ts'
import { collect, parseCollectSpec } from '../src/evidence/collect.ts'
import { McpClient } from '../src/evidence/mcp-client.ts'

const [specPath, kind, panel, url = process.env.RESCUE_MCP_MINE_DATA_URL ?? 'http://127.0.0.1:8101/mcp', since] = process.argv.slice(2)
if (!specPath || !kind || !panel) {
  console.error('用法：node tools/collect.ts <collect.yaml> <kind> <panel> [URL]')
  process.exit(2)
}
const client = new McpClient({ server: 'mine_data', url, token: process.env.RESCUE_MCP_MINE_DATA_TOKEN })
const ev = await collect((t, a) => client.call(t, a), parseCollectSpec(readFileSync(specPath, 'utf8'), specPath), kind, panel, since ? { since } : {})
process.stdout.write(JSON.stringify({ content_sha256: contentSha256(ev), canonical: canonicalJson(ev) }) + '\n')
