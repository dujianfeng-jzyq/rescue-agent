/**
 * 火灾蔓延第二意见（第七版）：证据插件按 fire_sim_request 的结果记录调用 fire_sim，存成证据记录，再由 fire_sim_compare 判读比较。
 * 用真实的 fire-assessment 技能和自检样例的记录；fire_sim 用假的（正常返回或抛错）。
 */

import assert from 'node:assert/strict'
import { mkdtemp, readFile } from 'node:fs/promises'
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

async function setup(fireSim?: (tool: string, args: Record<string, unknown>) => Promise<any>, requestCase = 'fire_sim_compare/s4-escalation-60h-red-service.input.json') {
  const root = await mkdtemp(join(tmpdir(), 'fire-sim-'))
  const ev = new Evidence({
    skillsDir: SKILLS,
    recordDir: join(root, 'records'),
    python: PYTHON,
    mineData: async () => {
      throw new Error('不应读矿井')
    },
    ...(fireSim ? { fireSim } : {}),
  })
  const input = await json(requestCase)
  const put = (kind: 'evidence' | 'result', content: Record<string, unknown>, source: Record<string, unknown>) =>
    ev.store.put(buildRecord({ kind, panel: '1201', asOf: input.evidence.as_of, origin: ORIGIN, source, content }))
  const E = await put('evidence', input.evidence, { skill: 'fire-assessment', collect_kind: 'assessment' })
  const P = await put('result', input.personnel, { skill: 'fire-assessment', script: 'personnel' })
  const RQ = await put('result', input.fire_sim_request, { skill: 'fire-assessment', script: 'fire_sim_request' })
  return { ev, input, E, P, RQ }
}

const compare = (ev: Evidence, E: string, P: string, RQ: string, S: string) =>
  ev
    .runScript({ skill: 'fire-assessment', script: 'fire_sim_compare', inputs: { evidence: E, personnel: P, fire_sim_request: RQ, fire_sim: S }, origin: ORIGIN })
    .then((x) => (x.record.content as any).result)

describe('火灾蔓延第二意见：collectFireSim → fire_sim_compare', () => {
  it('调用一次并存证据，比较结果与自检样例一致', async () => {
    const calls: [string, Record<string, unknown>][] = []
    const { ev, input, E, P, RQ } = await setup(async (tool, args) => {
      calls.push([tool, args])
      return input.fire_sim.result
    })
    assert.equal(ev.fireSimConfigured, true)
    const sim = await ev.collectFireSim({ request: RQ.record_id, origin: ORIGIN })
    assert.equal(sim.kind, 'evidence')
    assert.deepEqual(sim.parent_ids, [RQ.record_id])
    assert.equal((sim.source as any).collect_kind, 'fire_sim')
    assert.deepEqual(calls, [['simulate_fire', input.fire_sim_request.result.request]])
    assert.equal((sim.content as any).error, null)
    const r = await compare(ev, E.record_id, P.record_id, RQ.record_id, sim.record_id)
    const want = (await json('fire_sim_compare/s4-escalation-60h-red-service.expected.json')).result
    assert.deepEqual(r.lines, want.lines)
    assert.equal(r.status, 'ok')
  })

  it('调用失败记在 error 里，不抛错；比较结果为 unavailable', async () => {
    const { ev, E, P, RQ } = await setup(async () => {
      throw new McpError('timeout', 'fire_sim', '请求超过 90000 毫秒')
    })
    const sim = await ev.collectFireSim({ request: RQ.record_id, origin: ORIGIN })
    assert.equal((sim.content as any).error, '[fire_sim] 请求超过 90000 毫秒')
    const r = await compare(ev, E.record_id, P.record_id, RQ.record_id, sim.record_id)
    assert.equal(r.status, 'unavailable')
    assert.deepEqual(r.lines, ['第二意见不可用：[fire_sim] 请求超过 90000 毫秒'])
  })

  it('没有配置时不能调用；不是 fire_sim_request 的记录、不需要推演的请求都不收', async () => {
    const a = await setup()
    assert.equal(a.ev.fireSimConfigured, false)
    await assert.rejects(a.ev.collectFireSim({ request: a.RQ.record_id, origin: ORIGIN }), /没有配置外部火灾推演服务/)
    const b = await setup(async () => ({}))
    await assert.rejects(b.ev.collectFireSim({ request: b.P.record_id, origin: ORIGIN }), /不是 fire-assessment\/fire_sim_request 的结果/)
    const c = await setup(async () => ({}), 'fire_sim_compare/s5-not-needed.input.json')
    await assert.rejects(c.ev.collectFireSim({ request: c.RQ.record_id, origin: ORIGIN }), /不需要火灾推演/)
  })
})
