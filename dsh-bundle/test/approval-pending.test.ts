import assert from 'node:assert/strict'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, it } from 'node:test'

import { apply, loadPolicies } from '../src/approval/index.ts'
import { IncidentStore } from '../src/incident/store.ts'

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
})

async function setup(summaryIncident: string | null) {
  const dir = await mkdtemp(join(tmpdir(), 'appr-'))
  const store = new IncidentStore(dir)
  const iid = await store.open('2026-11-05T08:00:00', { category: 'fire', panel: '1201', summary: 'x' })
  const handlers: Record<string, (...a: any[]) => any> = {}
  const ctx: any = { on: (name: string, fn: any) => (handlers[name] = fn), logger: { warn: () => {} } }
  globalThis.fetch = (async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      ok: true,
      problems: [],
      summary: { action: '发布 1201 红色预警', tool: 'publish_warning', record_ids: ['RS-20261105T080000-aaaaaa'], checks: [], human_items: [], incident_id: summaryIncident === null ? null : iid },
    }),
  })) as any
  apply(ctx, { gatedServer: 'actions', policies: loadPolicies(), precheckUrl: 'http://x/redlines/check', precheckTimeoutMs: 1000, incidentDir: dir })
  const preExecute = async () => handlers['tools/pre-execute']!({ name: 'mcp__actions__publish_warning', arguments: {}, callId: 'call-1', signal: new AbortController().signal }, async () => ({ kind: 'allow' }))
  return { store, iid, handlers, preExecute }
}

describe('人工确认的等待和结果记进事件时间线', () => {
  it('弹确认前写 approval_pending（带确认人、超时、升级对象），有结果后写 approval_decided', async () => {
    const { store, iid, handlers, preExecute } = await setup('yes')
    assert.equal((await preExecute()).kind, 'ask')
    let release!: (v: string) => void
    const waiting = handlers['approval/request']!({ callId: 'call-1', toolName: 'mcp__actions__publish_warning' }, () => new Promise<string>((r) => (release = r)))
    await new Promise((r) => setTimeout(r, 50))
    let v = await store.get(iid)
    assert.equal(v.pending_approvals.length, 1)
    assert.deepEqual([v.pending_approvals[0]!.tool, v.pending_approvals[0]!.confirmer, v.pending_approvals[0]!.timeout_min, v.pending_approvals[0]!.escalate_to], ['publish_warning', '通风部门负责人', 10, '值班长'])
    release('rejected')
    assert.equal(await waiting, 'rejected')
    v = await store.get(iid)
    assert.deepEqual(v.pending_approvals, [])
    const decided = (await store.entries(iid)).find((e) => e.data.type === 'approval_decided')!
    assert.equal(decided.data.outcome, 'rejected')
  })

  it('预检摘要没有事件编号（没有未关闭事件）时什么都不记，确认照常', async () => {
    const { store, iid, handlers, preExecute } = await setup(null)
    assert.equal((await preExecute()).kind, 'ask')
    assert.equal(await handlers['approval/request']!({ callId: 'call-1', toolName: 'mcp__actions__publish_warning' }, async () => 'allowed-once'), 'allowed-once')
    assert.equal((await store.entries(iid)).length, 1)
  })
})
