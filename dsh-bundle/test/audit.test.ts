import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { toLine, toRecord } from '../src/audit/record.ts'

const base = { callId: 'c1', name: 'mcp__actions__issue_sealing_plan', sessionId: 's1', arguments: { plan: { plan_id: 'P-1' } } }
const now = new Date('2026-11-02T10:00:00Z')

describe('toRecord', () => {
  it('成功 → issued', () => {
    const r = toRecord({ ...base, isError: false }, now)
    assert.equal(r.outcome, 'issued')
    assert.equal(r.detail, null)
  })

  it('红线预检拦截 → denied', () => {
    const r = toRecord({ ...base, isError: true, errorCode: 'RESCUE_RED_LINE', errorMessage: '红线预检未通过' }, now)
    assert.equal(r.outcome, 'denied')
  })

  it('指挥员拒绝（DSH 无错误码，按消息识别）→ denied', () => {
    const r = toRecord({ ...base, isError: true, errorMessage: 'the user rejected tool "mcp__actions__issue_sealing_plan"' }, now)
    assert.equal(r.outcome, 'denied')
  })

  it('actions 服务端红线拦截 → denied', () => {
    const r = toRecord({ ...base, isError: true, errorMessage: 'Error executing tool issue_sealing_plan: 指令未发布。' }, now)
    assert.equal(r.outcome, 'denied')
  })

  it('其他错误 → failed', () => {
    const r = toRecord({ ...base, isError: true, errorMessage: 'connection reset' }, now)
    assert.equal(r.outcome, 'failed')
  })

  it('一条记录一行 JSON', () => {
    const line = toLine(toRecord({ ...base, isError: false }, now))
    assert.ok(line.endsWith('\n'))
    assert.equal(JSON.parse(line).session, 's1')
  })
})
