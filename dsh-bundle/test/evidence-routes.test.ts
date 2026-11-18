/**
 * 避灾路线第二意见（第六版）：证据插件按 route_request 的结果记录调用 route_planner，存成证据记录，再由 route_compare 比较。
 * 用真实的 fire-assessment 技能和自检样例的记录；route_planner 用假的（一组正常、一组抛错）。
 */

import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import { McpError } from '../src/evidence/mcp-client.ts'
import { buildRecord } from '../src/evidence/records.ts'
import { Evidence } from '../src/evidence/service.ts'

const PYTHON = process.env.RESCUE_TEST_PYTHON ?? new URL('../../mcp/.venv/bin/python', import.meta.url).pathname
const SKILLS = new URL('../../skills', import.meta.url).pathname
const CASES = join(SKILLS, 'fire-assessment/tests/cases')
const ORIGIN = { channel: 'web', trigger: 'chat' } as const
const json = async (p: string) => JSON.parse(await readFile(join(CASES, p), 'utf8'))

async function setup(routePlanner?: (tool: string, args: Record<string, unknown>) => Promise<any>) {
  const root = await mkdtemp(join(tmpdir(), 'routes-'))
  const ev = new Evidence({
    skillsDir: SKILLS,
    recordDir: join(root, 'records'),
    python: PYTHON,
    mineData: async () => {
      throw new Error('不应读矿井')
    },
    ...(routePlanner ? { routePlanner } : {}),
  })
  const input = await json('route_compare/s4-escalation-60h-red-service.input.json')
  const put = (kind: 'evidence' | 'result', content: Record<string, unknown>, source: Record<string, unknown>) =>
    ev.store.put(buildRecord({ kind, panel: '1201', asOf: input.evidence.as_of, origin: ORIGIN, source, content }))
  const E = await put('evidence', input.evidence, { skill: 'fire-assessment', collect_kind: 'assessment' })
  const P = await put('result', input.personnel, { skill: 'fire-assessment', script: 'personnel' })
  const RQ = await put('result', input.route_request, { skill: 'fire-assessment', script: 'route_request' })
  return { ev, input, E, P, RQ }
}

describe('避灾路线第二意见：collectRoutes → route_compare', () => {
  it('逐组调用并存证据；某组失败记在该组，不抛错；比较结果为 partial', async () => {
    const calls: Record<string, unknown>[] = []
    const { ev, input, E, P, RQ } = await setup(async (tool, args) => {
      assert.equal(tool, 'plan_escape_routes')
      calls.push(args)
      if (args.start === 'R-1201-RET#mid') throw new McpError('timeout', 'route_planner', '请求超过 30000 毫秒')
      return input.routes.results[0].result
    })
    assert.equal(ev.routesConfigured, true)
    const routes = await ev.collectRoutes({ request: RQ.record_id, origin: ORIGIN })
    assert.equal(routes.kind, 'evidence')
    assert.deepEqual(routes.parent_ids, [RQ.record_id])
    assert.equal((routes.source as any).collect_kind, 'routes')
    assert.deepEqual(calls, input.route_request.result.requests.map((r: any) => r.request))
    const c = routes.content as any
    assert.equal(c.results.length, 2)
    assert.equal(c.results[1].error, '[route_planner] 请求超过 30000 毫秒')

    const { record } = await ev.runScript({
      skill: 'fire-assessment',
      script: 'route_compare',
      inputs: { evidence: E.record_id, personnel: P.record_id, route_request: RQ.record_id, routes: routes.record_id },
      origin: ORIGIN,
    })
    const r = (record.content as any).result
    assert.equal(r.status, 'partial')
    assert.deepEqual(r.counts, { same_route: 1, service_unavailable: 1 })
    assert.match(r.lines[1], /外部服务不可用：\[route_planner\] 请求超过/)
  })

  it('没有配置时不能调用；不是 route_request 的记录不收', async () => {
    const a = await setup()
    assert.equal(a.ev.routesConfigured, false)
    await assert.rejects(a.ev.collectRoutes({ request: a.RQ.record_id, origin: ORIGIN }), /没有配置外部路径规划服务/)
    const b = await setup(async () => ({}))
    await assert.rejects(b.ev.collectRoutes({ request: b.P.record_id, origin: ORIGIN }), /不是 fire-assessment\/route_request 的结果/)
  })
})
