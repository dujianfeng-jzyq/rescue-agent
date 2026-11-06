import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { loadPolicies } from '../src/approval/index.ts'
import { type ActionPolicy, type ApprovalConfig, askVerdict, classify, type FetchLike, levelOf, precheck } from '../src/approval/policy.ts'

// 用契约导出的真实分级表（data/action-policy.json），测试和线上用同一份
const policies = loadPolicies()
const config: ApprovalConfig = {
  gatedServer: 'actions',
  policies,
  precheckUrl: 'http://precheck.test/redlines/check',
  precheckTimeoutMs: 200,
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

describe('classify（动作分级：C 级弹确认，A、B 级不弹，未登记按 C 级）', () => {
  it('只拦 guard 里 C 级的对外动作', () => {
    assert.deepEqual(classify('mcp__actions__issue_sealing_plan', config), { gated: true, rawName: 'issue_sealing_plan' })
    assert.deepEqual(classify('mcp__fire_model__assess_warning_level', config), { gated: false })
    assert.deepEqual(classify('subagent', config), { gated: false })
  })
  it('A 级只读工具不弹确认（不再靠名字前缀）', () => {
    assert.deepEqual(classify('mcp__actions__precheck_plan', config), { gated: false })
    assert.deepEqual(classify('mcp__actions__query_last_issued', config), { gated: false })
  })
  it('没有登记的工具按 C 级处理（fail closed），哪怕名字像只读', () => {
    assert.deepEqual(classify('mcp__actions__query_something_new', config), { gated: true, rawName: 'query_something_new' })
    assert.deepEqual(classify('mcp__actions__precheck_new_thing', config), { gated: true, rawName: 'precheck_new_thing' })
    assert.equal(levelOf('brand_new_action', policies), 'C')
  })
  it('B 级（内部提示）不弹确认', () => {
    const withB: ActionPolicy[] = [...policies, { tool: 'notify_duty_officer', level: 'B', name: '值班提示' }]
    assert.deepEqual(classify('mcp__actions__notify_duty_officer', { gatedServer: 'actions', policies: withB }), { gated: false })
  })
  it('五个对外动作都是 C 级，每个都有确认人和批准超时', () => {
    const c = policies.filter((p) => p.level === 'C')
    assert.deepEqual(c.map((p) => p.tool).sort(), ['escalate_emergency', 'issue_dispatch_order', 'issue_sealing_plan', 'notify_evacuation', 'publish_warning'])
    for (const p of c) {
      assert.ok(p.confirmer, `${p.tool} 缺确认人`)
      assert.ok((p.approval_timeout_min ?? 0) > 0, `${p.tool} 缺批准超时`)
      assert.ok(p.escalate_to, `${p.tool} 缺超时升级对象`)
    }
  })
})

describe('askVerdict', () => {
  it('提示中写明确认人', () => {
    const v = askVerdict('escalate_emergency', config)
    assert.equal(v.kind, 'ask')
    assert.match(v.kind === 'ask' ? v.reason : '', /总工程师或矿长/)
    assert.match(v.kind === 'ask' ? v.reason : '', /升级为应急响应/)
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

  it('预检说明（如人工改等级的理由）显示在确认提示里', async () => {
    const { fetch } = fakeFetch(() => ({ json: { ok: true, problems: [], notes: ['与计算结果不一致：计算 红色，提交 橙色，理由：已撤人'] } }))
    const v = await precheck('publish_warning', { notice: {} }, config, signal, fetch)
    assert.equal(v.kind, 'ask')
    assert.match(v.kind === 'ask' ? v.reason : '', /计算 红色，提交 橙色/)
    assert.match(v.kind === 'ask' ? v.displayReason.zh : '', /理由：已撤人/)
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

describe('ApprovalSummary 生成确认提示', () => {
  it('列出动作、记录、逐项核对和把关事项，不透出原始参数', async () => {
    const summary = {
      action: '发布 1201 工作面采空区 红色预警',
      tool: 'publish_warning',
      record_ids: ['RS-20261104T200000-abc123'],
      checks: [
        { name: '引用记录', ok: true, detail: 'RS-20261104T200000-abc123（fire-assessment/warning）' },
        { name: '预警等级', ok: true, detail: '与计算结果一致（红色）' },
      ],
      human_items: ['与计算结果不一致：……'],
      override_reason: null,
    }
    const { fetch } = fakeFetch(() => ({ json: { ok: true, problems: [], notes: [], summary } }))
    const v = await precheck('publish_warning', { notice: { secret: 'x' } }, config, new AbortController().signal, fetch)
    assert.equal(v.kind, 'ask')
    if (v.kind !== 'ask') return
    assert.match(v.reason, /^发布 1201 工作面采空区 红色预警：需由【通风部门负责人】确认后执行/)
    assert.match(v.reason, /依据记录：RS-20261104T200000-abc123/)
    assert.match(v.reason, /✓ 预警等级：与计算结果一致（红色）/)
    assert.match(v.reason, /请确认人把关：与计算结果不一致/)
    assert.doesNotMatch(v.reason, /secret/)
  })
})
