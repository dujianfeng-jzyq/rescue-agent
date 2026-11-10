import assert from 'node:assert/strict'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, it } from 'node:test'

import { apply, loadPolicies } from '../src/approval/index.ts'
import { timeoutOutcome } from '../src/approval/pending.ts'
import type { ActionPolicy } from '../src/approval/policy.ts'
import { IncidentStore } from '../src/incident/store.ts'

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
})

/** 批准超时 0.001 分钟（60 毫秒），on_timeout 由用例指定 */
async function setup(onTimeout: ActionPolicy['on_timeout']) {
  const dir = await mkdtemp(join(tmpdir(), 'appr-to-'))
  const store = new IncidentStore(dir)
  const iid = await store.open('2026-11-05T08:00:00', { category: 'fire', panel: '1201', summary: 'x' })
  const handlers: Record<string, (...a: any[]) => any> = {}
  const ctx: any = { on: (name: string, fn: any) => (handlers[name] = fn), logger: { warn: () => {}, info: () => {} } }
  globalThis.fetch = (async () => ({
    ok: true,
    status: 200,
    json: async () => ({ ok: true, problems: [], summary: { action: '加密监测', tool: 'publish_warning', record_ids: ['RS-1'], checks: [], human_items: [], incident_id: iid } }),
  })) as any
  const policies = loadPolicies().map((p) => (p.tool === 'publish_warning' ? { ...p, approval_timeout_min: 0.001, ...(onTimeout ? { on_timeout: onTimeout } : {}) } : p))
  apply(ctx, { gatedServer: 'actions', policies, precheckUrl: 'http://x/redlines/check', precheckTimeoutMs: 1000, incidentDir: dir })
  const ask = async () => {
    const v = await handlers['tools/pre-execute']!({ name: 'mcp__actions__publish_warning', arguments: {}, callId: 'c1', signal: new AbortController().signal }, async () => ({ kind: 'allow' }))
    assert.equal(v.kind, 'ask')
  }
  return { store, iid, handlers, ask }
}

const decided = async (store: IncidentStore, iid: string) => (await store.entries(iid)).find((e) => e.data.type === 'approval_decided')

describe('批准超时自动处理（on_timeout）', () => {
  it('approve：没人回应，到点自动放行，结果记 auto: timeout，确认不再未决', async () => {
    const { store, iid, handlers, ask } = await setup('approve')
    await ask()
    const out = await handlers['approval/request']!({ callId: 'c1' }, () => new Promise<string>(() => {})) // 人一直不回应
    assert.equal(out, 'allowed-once')
    const d = (await decided(store, iid))!
    assert.deepEqual([d.data.outcome, d.data.auto], ['allowed-once', 'timeout'])
    assert.deepEqual((await store.get(iid)).pending_approvals, [])
  })

  it('reject：到点自动拒绝', async () => {
    const { store, iid, handlers, ask } = await setup('reject')
    await ask()
    assert.equal(await handlers['approval/request']!({ callId: 'c1' }, () => new Promise<string>(() => {})), 'rejected')
    assert.equal((await decided(store, iid))!.data.auto, 'timeout')
  })

  it('人在超时前回应：按人的结果，不记 auto', async () => {
    const { store, iid, handlers, ask } = await setup('approve')
    await ask()
    assert.equal(await handlers['approval/request']!({ callId: 'c1' }, async () => 'rejected'), 'rejected')
    const d = (await decided(store, iid))!
    assert.equal(d.data.outcome, 'rejected')
    assert.equal('auto' in d.data, false)
  })

  it('escalate（缺省）：超时也不自动处理，一直等人', async () => {
    const { store, iid, handlers, ask } = await setup(undefined)
    await ask()
    let release!: (v: string) => void
    const waiting = handlers['approval/request']!({ callId: 'c1' }, () => new Promise<string>((r) => (release = r)))
    await new Promise((r) => setTimeout(r, 300)) // 远超 60 毫秒
    assert.equal((await store.get(iid)).pending_approvals.length, 1)
    release('allowed-once')
    assert.equal(await waiting, 'allowed-once')
  })

  it('待确认的记录里带 on_timeout，监听器据此决定要不要兜底升级', async () => {
    const { store, iid, handlers, ask } = await setup('approve')
    await ask()
    void handlers['approval/request']!({ callId: 'c1' }, () => new Promise<string>(() => {}))
    await new Promise((r) => setTimeout(r, 20))
    const pending = (await store.entries(iid)).find((e) => e.data.type === 'approval_pending')!
    assert.equal(pending.data.on_timeout, 'approve')
  })

  it('timeoutOutcome：没有批准超时或缺省不自动处理', () => {
    assert.equal(timeoutOutcome(undefined), undefined)
    assert.equal(timeoutOutcome({ tool: 'x', level: 'C', name: 'x', on_timeout: 'approve' }), undefined)
    assert.equal(timeoutOutcome({ tool: 'x', level: 'C', name: 'x', approval_timeout_min: 5, on_timeout: 'escalate' }), undefined)
    assert.deepEqual(timeoutOutcome({ tool: 'x', level: 'C', name: 'x', approval_timeout_min: 5, on_timeout: 'approve' }), { ms: 300_000, outcome: 'allowed-once' })
  })
})
