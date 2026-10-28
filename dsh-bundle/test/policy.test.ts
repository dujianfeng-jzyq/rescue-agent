import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { type ApprovalConfig, askVerdict, classify, DEFAULT_CONFIRMERS, type FetchLike, precheck } from '../src/approval/policy.ts'

const config: ApprovalConfig = {
  gatedServer: 'actions',
  exemptPrefixes: ['check_'],
  precheckUrl: 'http://precheck.test/redlines/check',
  precheckTimeoutMs: 200,
  confirmers: DEFAULT_CONFIRMERS,
}

const signal = new AbortController().signal

function fakeFetch(respond: (body: { tool: string }) => { status?: number; json: unknown } | Error): { fetch: FetchLike; calls: unknown[] } {
  const calls: unknown[] = []
  const fetch: FetchLike = async (url, init) => {
    const body = JSON.parse(init.body)
    calls.push({ url, body, headers: init.headers })
    const r = respond(body)
    if (r instanceof Error) throw r
    return { ok: (r.status ?? 200) < 400, status: r.status ?? 200, json: async () => r.json }
  }
  return { fetch, calls }
}

describe('classify', () => {
  it('只拦 actions 服务的指令类工具', () => {
    assert.deepEqual(classify('mcp__actions__issue_sealing_plan', config), { gated: true, rawName: 'issue_sealing_plan' })
    assert.deepEqual(classify('mcp__actions__check_plan_redlines', config), { gated: false })
    assert.deepEqual(classify('mcp__fire_model__assess_warning_level', config), { gated: false })
    assert.deepEqual(classify('subagent', config), { gated: false })
  })
})

describe('askVerdict', () => {
  it('提示中写明确认人', () => {
    const v = askVerdict('issue_firefighting_plan', config)
    assert.equal(v.kind, 'ask')
    assert.match(v.kind === 'ask' ? v.reason : '', /总工程师/)
  })
  it('未配置的工具也要求确认', () => {
    const v = askVerdict('some_new_action', config)
    assert.equal(v.kind, 'ask')
  })
})

describe('precheck', () => {
  it('预检通过 → ask', async () => {
    const { fetch, calls } = fakeFetch(() => ({ json: { ok: true, problems: [] } }))
    const v = await precheck('issue_sealing_plan', { plan: {} }, config, signal, fetch)
    assert.equal(v.kind, 'ask')
    assert.deepEqual((calls[0] as { body: unknown }).body, { tool: 'issue_sealing_plan', arguments: { plan: {} } })
  })

  it('红线违规 → deny，并带出条号', async () => {
    const { fetch } = fakeFetch(() => ({ json: { ok: false, problems: ['- [第85条] 步骤2：缺少注惰'] } }))
    const v = await precheck('issue_sealing_plan', {}, config, signal, fetch)
    assert.equal(v.kind, 'deny')
    assert.match(v.kind === 'deny' ? v.reason : '', /第85条/)
    assert.equal(v.kind === 'deny' ? v.code : '', 'RESCUE_RED_LINE')
  })

  it('预检服务不可用 → deny（fail closed）', async () => {
    const { fetch } = fakeFetch(() => new Error('ECONNREFUSED'))
    const v = await precheck('issue_sealing_plan', {}, config, signal, fetch)
    assert.equal(v.kind === 'deny' ? v.code : '', 'RESCUE_PRECHECK_UNAVAILABLE')
  })

  it('预检返回 HTTP 错误或格式不对 → deny', async () => {
    for (const r of [{ status: 500, json: {} }, { json: { ok: 'yes' } }]) {
      const { fetch } = fakeFetch(() => r)
      const v = await precheck('issue_sealing_plan', {}, config, signal, fetch)
      assert.equal(v.kind, 'deny')
    }
  })

  it('预检超时 → deny', async () => {
    const slow: FetchLike = (_url, init) =>
      new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(new Error('timeout'))))
    // AbortSignal.timeout 的定时器不会让进程保持运行；测试里手动保活（DSH 宿主进程本身常驻，不需要）
    const keepAlive = setTimeout(() => {}, 5000)
    const v = await precheck('issue_sealing_plan', {}, config, signal, slow)
    clearTimeout(keepAlive)
    assert.equal(v.kind, 'deny')
  })

  it('配置了令牌时带认证头', async () => {
    const { fetch, calls } = fakeFetch(() => ({ json: { ok: true, problems: [] } }))
    await precheck('publish_warning', {}, { ...config, token: 't0k' }, signal, fetch)
    assert.equal((calls[0] as { headers: Record<string, string> }).headers.Authorization, 'Bearer t0k')
  })
})
