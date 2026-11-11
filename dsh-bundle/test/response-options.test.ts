import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { describeOptions, loadResponseOptions, normalizeLevel, planKeysFor, priorChoice } from '../src/fire-loop/response-options.ts'

const levels = loadResponseOptions()

describe('处置选项表（契约导出）', () => {
  it('橙色卡片只列可选的处置，必做项写在说明里；标签换回编号', () => {
    const d = describeOptions(levels, '橙色')
    assert.equal(d.level, 'orange')
    assert.deepEqual(d.card!.options, ['注浆', '堵漏', '不追加（只做必做项）']) // “不追加”由代码给，智能体不自己加
    assert.equal(d.card!.multiSelect, true)
    assert.match(d.card!.detail!, /注氮/)
    assert.match(d.card!.question, /不追加（只做必做项）/)
    assert.match(d.note, /跳过/)
    assert.deepEqual(d.card!.label_to_id, { 注浆: 'grout', 堵漏: 'leak_seal', '不追加（只做必做项）': null })
    assert.equal(d.options.length, 3)
  })

  it('黄色、红色没有卡片；其他等级没有选项', () => {
    assert.equal(describeOptions(levels, 'yellow').card, null)
    assert.equal(describeOptions(levels, '红色').card, null)
    assert.deepEqual(describeOptions(levels, 'blue').options, [])
    assert.equal(normalizeLevel('黄色'), 'yellow')
  })

  it('选项编号换成方案的可选步骤 key；必做项忽略，未知或不能选的报错', () => {
    assert.deepEqual(planKeysFor(levels, 'orange', ['inert', 'grout', 'leak_seal']), ['grouting', 'leak_sealing'])
    assert.deepEqual(planKeysFor(levels, 'orange', []), [])
    assert.deepEqual(planKeysFor(levels, 'orange', ['grout', 'grout']), ['grouting'])
    assert.throws(() => planKeysFor(levels, 'orange', ['water_fire']), /不在 orange 的处置选项表里/)
    assert.throws(() => planKeysFor(levels, 'red', ['evacuate']), /不由卡片选择/)
  })
})

describe('橙色是否再问（priorChoice，按事件时间线）', () => {
  const plan = (at: string, status = 'issued') => ({ at, kind: 'note', by: 'guard', data: { type: 'action', tool: 'issue_firefighting_plan', status } })
  const review = (at: string, overall: string) => ({ at, kind: 'note', by: 'flow', data: { type: 'effect_review', overall } })

  it('没下发过方案照常问；被拒的不算', () => {
    assert.equal(priorChoice([], 'orange').ask, true)
    assert.equal(priorChoice([plan('2026-11-03T20:00:00', 'rejected')], '橙色').ask, true)
  })

  it('下发之后没复评或复评没恶化：不再问', () => {
    assert.equal(priorChoice([plan('2026-11-03T20:00:00')], 'orange').ask, false)
    const p = priorChoice([plan('2026-11-03T20:00:00'), review('2026-11-03T22:00:00', 'continuing')], 'orange')
    assert.equal(p.ask, false)
    assert.equal(p.review, '2026-11-03T22:00:00')
  })

  it('下发之后最近一次复评恶化：问（追加）；追加方案下发后又不问', () => {
    const t = [plan('2026-11-03T20:00:00'), review('2026-11-03T22:00:00', 'worsening')]
    assert.equal(priorChoice(t, 'orange').ask, true)
    assert.equal(priorChoice([...t, review('2026-11-04T00:00:00', 'continuing')], 'orange').ask, false)
    assert.equal(priorChoice([...t, plan('2026-11-03T22:30:00')], 'orange').ask, false)
  })

  it('其他等级不按卡片判断', () => {
    assert.equal(priorChoice([plan('2026-11-03T20:00:00')], 'red').ask, true)
  })
})
