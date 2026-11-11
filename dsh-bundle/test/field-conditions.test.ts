import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { describeFieldCard, loadFieldConditions, resolveFieldChoices } from '../src/fire-loop/field-conditions.ts'

const fc = loadFieldConditions()

describe('封闭方案现场条件卡片（契约导出）', () => {
  it('5 项现场条件 + 确认人，各自带选项；问题 id 就是字段名', () => {
    const card = describeFieldCard(fc)
    assert.deepEqual(card.questions.map((q) => q.id), ['area_temperature_c', 'visibility_m', 'fire_source_confirmed', 'water_supply_sufficient', 'return_airway_clear', 'confirmed_by'])
    assert.deepEqual(card.questions[2]!.options, [{ label: '是' }, { label: '否' }])
    assert.deepEqual(card.questions[5]!.options, [{ label: '总指挥' }, { label: '总工程师' }, { label: '救护队带队指挥员' }])
    assert.ok(card.questions.every((q) => q.multi_select === false && q.header === '现场条件'))
    assert.match(card.note, /不要替人/)
  })

  it('答案原文换成数值或 true/false，确认人写进每一项', () => {
    const r = resolveFieldChoices(fc, [
      { field: 'area_temperature_c', choice: '45 ℃' },
      { field: 'visibility_m', choice: '12' }, // 在“其他”里输入的实际数值
      { field: 'fire_source_confirmed', choice: '是' },
      { field: 'return_airway_clear', choice: '否' },
    ], '总指挥')
    assert.deepEqual(r, [
      { field: 'area_temperature_c', value: 45, confirmed_by: '总指挥' },
      { field: 'visibility_m', value: 12, confirmed_by: '总指挥' },
      { field: 'fire_source_confirmed', value: true, confirmed_by: '总指挥' },
      { field: 'return_airway_clear', value: false, confirmed_by: '总指挥' },
    ])
    assert.deepEqual(resolveFieldChoices(fc, [{ field: 'area_temperature_c', choice: '38.5℃' }], ' 总工程师 '), [{ field: 'area_temperature_c', value: 38.5, confirmed_by: '总工程师' }])
  })

  it('全部跳过（没有答案）不报错、不产生输入', () => {
    assert.deepEqual(resolveFieldChoices(fc, [], undefined), [])
  })

  it('换不了的答案、缺确认人、未知或重复字段都报错，不让智能体猜', () => {
    assert.throws(() => resolveFieldChoices(fc, [{ field: 'fire_source_confirmed', choice: '大概是' }], '总指挥'), /不是“是”或“否”/)
    assert.throws(() => resolveFieldChoices(fc, [{ field: 'visibility_m', choice: '很好' }], '总指挥'), /不是数值/)
    assert.throws(() => resolveFieldChoices(fc, [{ field: 'visibility_m', choice: '' }], '总指挥'), /不是数值/)
    assert.throws(() => resolveFieldChoices(fc, [{ field: 'visibility_m', choice: '20 米' }], undefined), /confirmed_by/)
    assert.throws(() => resolveFieldChoices(fc, [{ field: 'methane_pct', choice: '1' }], '总指挥'), /不是现场条件/)
    assert.throws(() => resolveFieldChoices(fc, [{ field: 'visibility_m', choice: '5' }, { field: 'visibility_m', choice: '6' }], '总指挥'), /重复/)
  })
})
