import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { reviewEffect } from '../src/fire-loop/effect.ts'
import type { LastIssued } from '../src/fire-loop/effect.ts'

/** 假的证据服务：记下传给 effect.py 的 options，返回固定的结果 */
function fakeEvidence(seen: { options?: any }) {
  const result = {
    as_of: '2026-11-04T11:08:42', since: '2026-11-03T17:38:42', since_label: '观察起点（未采取处置）', since_source: 's', treated: false,
    overall: 'worsening', overall_name: '未采取处置：趋势恶化', observed_h: 17.5, counts: {}, counts_text: '恶化 8 个', next_review_at: '2026-11-04T23:08:42', escalation_suggested: true,
  }
  return {
    collectEvidence: async () => ({ record_id: 'RS-ev' }),
    runScript: async (o: any) => {
      seen.options = o.options
      return { record: { record_id: 'RS-effect', content: { result } } }
    },
    renderReport: async () => ({ record_id: 'RP-x', content: 'x' }),
  } as any
}

const origin = { channel: 'web', trigger: 'chat', caller: null, conversation_id: null } as any

describe('review_effect：有没有采取处置（2026-11-11 演示复看）', () => {
  it('台账里只有加密监测：treated=false，起算来源写“未采取处置”，摘要用观察起点', async () => {
    const seen: { options?: any } = {}
    const last: LastIssued = { found: true, tool: 'start_monitoring', action_id: 'A-1', issued_at: '2026-11-03T17:38:42' }
    const out = await reviewEffect(fakeEvidence(seen), { panel: '211016', origin, lastIssued: async () => last })
    assert.equal(seen.options.treated, false)
    assert.match(seen.options.since_source, /^未采取处置，从指令台账 A-1 加密监测的开始时间起观察/)
    assert.equal(out.result.treated, false)
    assert.match(out.card.summary, /^未采取处置：趋势恶化；观察起点（未采取处置） 2026-11-03T17:38:42/)
  })

  it('台账里有方案、或指挥人员给了 since：treated=true', async () => {
    const seen: { options?: any } = {}
    const plan: LastIssued = { found: true, tool: 'issue_sealing_plan', action_id: 'A-2', issued_at: '2026-11-04T17:08:42', plan_id: 'SP-1' }
    await reviewEffect(fakeEvidence(seen), { panel: '211016', origin, lastIssued: async () => plan })
    assert.equal(seen.options.treated, true)
    assert.match(seen.options.since_source, /^指令台账：A-2 封闭方案 SP-1 的下发时间/)
    await reviewEffect(fakeEvidence(seen), { panel: '211016', origin, since: '2026-11-04T00:00:00', lastIssued: async () => plan })
    assert.equal(seen.options.treated, true)
  })
})
