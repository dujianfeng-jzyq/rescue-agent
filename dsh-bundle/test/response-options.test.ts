import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { applyPriorChoice, describeOptions, interpretChoice, isNonePhrase, loadResponseOptions, normalizeLevel, planKeysFor, priorChoice } from '../src/fire-loop/response-options.ts'

const levels = loadResponseOptions()

describe('处置选项表（契约导出）', () => {
  it('橙色卡片列出全部处置（注氮标“建议”），没有“不采取处置”项（要在输入框输入）；标签换回编号', () => {
    const d = describeOptions(levels, '橙色')
    assert.equal(d.level, 'orange')
    assert.deepEqual(d.card!.options, [{ label: '注氮（建议）' }, { label: '注浆' }, { label: '堵漏' }]) // 不放“不采取处置”，免得与处置同时勾上冲突
    assert.equal(d.card!.multi_select, true) // 与 ask_user 参数同名同构，原样传
    assert.equal(d.card!.id, 'orange_response_options')
    assert.doesNotMatch(d.card!.question, /必做/)
    assert.match(d.card!.question, /输入框输入“不采取处置”后提交/)
    assert.match(d.card!.question, /跳过.*暂不决定/)
    assert.match(d.note, /record_response_choice/)
    assert.deepEqual(d.label_to_id, { '注氮（建议）': 'inert', 注浆: 'grout', 堵漏: 'leak_seal' })
    assert.equal(d.options.length, 3)
  })

  it('黄色、红色没有卡片；其他等级没有选项', () => {
    assert.equal(describeOptions(levels, 'yellow').card, null)
    assert.equal(describeOptions(levels, '红色').card, null)
    assert.deepEqual(describeOptions(levels, 'blue').options, [])
    assert.equal(normalizeLevel('黄色'), 'yellow')
  })

  it('选项编号换成方案的可选步骤 key（注氮也是可选步骤）；未知或不能选的报错', () => {
    assert.deepEqual(planKeysFor(levels, 'orange', ['inert', 'grout', 'leak_seal']), ['inert_injection', 'grouting', 'leak_sealing'])
    assert.deepEqual(planKeysFor(levels, 'orange', []), [])
    assert.deepEqual(planKeysFor(levels, 'orange', ['grout', 'grout']), ['grouting'])
    assert.throws(() => planKeysFor(levels, 'orange', ['water_fire']), /不在 orange 的处置选项表里/)
    assert.throws(() => planKeysFor(levels, 'red', ['evacuate']), /不由卡片选择/)
  })
})

describe('操作员的答案（interpretChoice，代码判断）', () => {
  it('勾了处置：按卡片标签（含“建议”后缀）或原名认；输入框另有文字只记下', () => {
    const r = interpretChoice(levels, 'orange', ['注氮（建议）', '堵漏'], '', false)
    assert.equal(r.choice, 'measures')
    assert.deepEqual(r.option_ids, ['inert', 'leak_seal'])
    assert.match(r.next, /draft_firefighting_plan.*\["inert","leak_seal"\]/)
    assert.deepEqual(interpretChoice(levels, '橙色', ['注浆'], '', false).option_ids, ['grout'])
    const t = interpretChoice(levels, 'orange', ['注浆'], '尽快', false)
    assert.equal(t.choice, 'measures')
    assert.match(t.text, /另附文字：尽快/)
  })
  it('只在输入框输入“不采取处置” → none（容忍首尾空白、引号、句末标点）', () => {
    const r = interpretChoice(levels, 'orange', [], '不采取处置', false)
    assert.equal(r.choice, 'none')
    assert.deepEqual(r.option_ids, [])
    assert.match(r.next, /不编方案、不下发/)
    for (const t of [' 不采取处置。', '“不采取处置”', '不采取处置!']) assert.equal(interpretChoice(levels, 'orange', [], t, false).choice, 'none', t)
    assert.equal(interpretChoice(levels, 'orange', ['不采取处置'], '', false).choice, 'none') // 放错到 selected 也认
  })
  it('意思相近的说法不算：暂不决定', () => {
    for (const t of ['不处置', '先不采取处置', '不采取处置，先观察', '不采取任何处置', '不采取操作']) {
      assert.equal(isNonePhrase(t), false, t)
      const r = interpretChoice(levels, 'orange', [], t, false)
      assert.equal(r.choice, 'undecided', t)
      assert.match(r.next, /须输入“不采取处置”/)
    }
  })
  it('勾了处置又输入“不采取处置” → 冲突：不下发、立即重问', () => {
    const r = interpretChoice(levels, 'orange', ['注浆'], '不采取处置', false)
    assert.equal(r.choice, 'conflict')
    assert.match(r.next, /不编方案、不下发.*get_response_options/)
  })
  it('点跳过、没有答案 → 暂不决定；跳过时忽略残留的勾选和文字', () => {
    assert.equal(interpretChoice(levels, 'orange', ['注氮（建议）'], '不采取处置', true).choice, 'undecided')
    assert.match(interpretChoice(levels, 'orange', [], '', true).text, /点了跳过/)
    assert.equal(interpretChoice(levels, 'orange', [], '', false).choice, 'undecided')
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

  const choice = (at: string, c: string, level = 'orange') => ({ at, kind: 'note', by: 'flow', data: { type: 'response_choice', level, choice: c } })

  it('操作员输入“不采取处置”：之后复评没恶化不再问，恶化再问', () => {
    const p = priorChoice([choice('2026-11-03T20:00:00', 'none')], 'orange')
    assert.equal(p.ask, false)
    assert.equal(p.declined_at, '2026-11-03T20:00:00')
    assert.equal(priorChoice([choice('2026-11-03T20:00:00', 'none'), review('2026-11-03T22:00:00', 'worsening')], 'orange').ask, true)
  })

  it('暂不决定：下次照常问；选了处置但还没下发也照常问', () => {
    assert.equal(priorChoice([choice('2026-11-03T20:00:00', 'undecided')], 'orange').ask, true)
    assert.equal(priorChoice([plan('2026-11-03T19:00:00'), choice('2026-11-03T20:00:00', 'undecided')], 'orange').ask, true)
    assert.equal(priorChoice([choice('2026-11-03T20:00:00', 'measures')], 'orange').ask, true)
    assert.equal(priorChoice([choice('2026-11-03T20:00:00', 'none', 'red')], 'orange').ask, true) // 别的等级的不算
  })

  it('选择冲突：再问，并标出冲突', () => {
    const p = priorChoice([choice('2026-11-03T20:00:00', 'conflict')], 'orange')
    assert.equal(p.ask, true)
    assert.equal(p.conflict, true)
  })

  it('卡片随之前的决定调整：不处置后复评恶化，卡片说明为什么再问；不用问就去掉卡片', () => {
    const card = () => ({ ...describeOptions(levels, 'orange') }) as any
    const declined = priorChoice([choice('2026-11-03T21:08:42', 'none'), review('2026-11-04T11:08:42', 'worsening')], 'orange')
    const asked = applyPriorChoice(card(), declined)
    assert.match(asked.card.question, /。上次（2026-11-03T21:08:42）选择不采取处置；此后 2026-11-04T11:08:42 的复评结论为恶化，所以再问一次$/)
    assert.deepEqual(asked.card.options, card().card.options, '选项不变')
    const quiet = applyPriorChoice(card(), priorChoice([choice('2026-11-03T21:08:42', 'none')], 'orange'))
    assert.equal(quiet.card, null)
    assert.match(quiet.note!, /不再问操作员/)
    const conflict = applyPriorChoice(card(), priorChoice([choice('2026-11-03T20:00:00', 'conflict')], 'orange'))
    assert.match(conflict.card.question, /请只选一种$/)
    const again = applyPriorChoice(card(), priorChoice([plan('2026-11-03T20:00:00'), review('2026-11-03T22:00:00', 'worsening')], 'orange'))
    assert.match(again.card.question, /已下发的处置不用再选$/)
    // 第一次问：卡片不变
    assert.equal(applyPriorChoice(card(), priorChoice([], 'orange')).card.question, card().card.question)
  })

  it('其他等级不按卡片判断', () => {
    assert.equal(priorChoice([plan('2026-11-03T20:00:00')], 'red').ask, true)
  })

  it('火区封闭方案已下发后不再问橙色处置，即使复评恶化；启封之后照常判断（2026-11-12）', () => {
    const act = (at: string, tool: string, status = 'issued') => ({ at, kind: 'note', by: 'guard', data: { type: 'action', tool, status } })
    const declined = choice('2026-11-03T21:11:23', 'none')
    const sealed = act('2026-11-04T15:11:23', 'issue_sealing_plan')
    const t = [declined, sealed, review('2026-11-04T17:11:23', 'worsening')]
    const p = priorChoice(t, 'orange')
    assert.equal(p.ask, false)
    assert.equal(p.sealed_at, '2026-11-04T15:11:23')
    const out = applyPriorChoice({ ...describeOptions(levels, 'orange') } as any, p)
    assert.equal(out.card, null)
    assert.match(out.note!, /已下发火区封闭方案/)
    assert.equal(priorChoice([declined, act('2026-11-04T15:11:23', 'issue_sealing_plan', 'rejected'), review('2026-11-04T17:11:23', 'worsening')], 'orange').ask, true, '被拒的封闭方案不算')
    assert.equal(priorChoice([...t, act('2026-11-20T08:00:00', 'approve_unsealing'), review('2026-11-20T10:00:00', 'worsening')], 'orange').ask, true, '启封之后复燃照常问')
  })
})
