import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { describeOptions, loadResponseOptions, normalizeLevel, planKeysFor } from '../src/fire-loop/response-options.ts'

const levels = loadResponseOptions()

describe('处置选项表（契约导出）', () => {
  it('橙色卡片只列可选的处置，必做项写在说明里；标签换回编号', () => {
    const d = describeOptions(levels, '橙色')
    assert.equal(d.level, 'orange')
    assert.deepEqual(d.card!.options, ['注浆', '堵漏'])
    assert.equal(d.card!.multiSelect, true)
    assert.match(d.card!.detail!, /注氮/)
    assert.match(d.card!.question, /跳过/) // 提交按钮在没选任何项时是灰的，不追加只能点跳过
    assert.match(d.note, /跳过/)
    assert.deepEqual(d.card!.label_to_id, { 注浆: 'grout', 堵漏: 'leak_seal' })
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
