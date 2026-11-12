import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { checkExplanation, numbersIn, type Options, stagePrompt, stageSchema, ungrounded } from '../src/fire-loop/explanation.ts'

const OPTIONS: Options = {
  focus_options: [
    { id: 'level', label: '预警等级 红色（危险）（红色（危险）：束管CO最大值(ppm) 1629.09 ≥ 1000）' },
    { id: 'explosion:BT-1201-G1', label: 'BT-1201-G1 II 区 减风危险区：可燃气体继续积聚会进入爆炸范围' },
    { id: 'personnel', label: '受威胁 11 人（R-1201-FACE 9 人；R-1201-RET 2 人）' },
  ],
  next_step_options: [
    { id: 'evacuate_per_plan', label: '提醒调度员按预案立即撤人' },
    { id: 'escalate_emergency', label: '提请指挥人员决定是否升级为应急响应' },
  ],
  limits: { max_focus: 2, max_next_steps: 2, max_note_chars: 40, max_summary_chars: 60 },
  evacuation_reminder: true,
}
const MATERIAL = '束管CO最大值(ppm) 1629.09 ≥ 1000；受威胁 11 人；BT-1201-G1 乙烯 4.397 ppm；2026-11-04 20:00:00'

describe('研判说明：数值核对', () => {
  it('编号、化学式、条号、日期时刻里的数字不算数值', () => {
    assert.deepEqual(numbersIn('BT-1201-G1 的 C2H4 按第75条，2026-11-04 20:00 记录 RS-20261104T200000-ab12cd，II 区'), [])
    assert.deepEqual(numbersIn('乙烯 4.397 ppm，受威胁 11 人'), ['4.397', '11'])
  })
  it('按数值比较；材料里没有的数值列出', () => {
    assert.deepEqual(ungrounded('CO 1629.090 ppm、11 人', MATERIAL), [])
    assert.deepEqual(ungrounded('约 1600 ppm，煤温约 150 ℃', MATERIAL), ['1600', '150'])
  })

  it('日期、时刻要在材料里出现；中文数字加单位判不合格（代码复查第 2 轮）', () => {
    const m = '证据时间 2026-11-04T20:00:00，处置开始 2026-11-04 08:30'
    assert.deepEqual(ungrounded('截至 2026-11-04 20:00，8:30 开始处置', m), [])
    assert.deepEqual(ungrounded('预计 14:30 到达，2026-11-05 复评', m), ['2026-11-05', '14:30'])
    assert.deepEqual(ungrounded('约两小时后到达，三人受威胁，观测十二 小时', m), ['两小时（请用阿拉伯数字照抄记录）', '三人（请用阿拉伯数字照抄记录）', '十二小时（请用阿拉伯数字照抄记录）'])
    assert.deepEqual(ungrounded('一旦氧浓度上升，进一步注氮；爆炸三角形无法判定，十分危险', m), [])
  })
})

describe('研判说明：结构化结果核对', () => {
  const good = {
    summary: '红色预警，受威胁 11 人，需立即按预案撤人',
    focus: [{ id: 'level', note: '束管 CO 1629.09 ppm 超过红色阈值' }, { id: 'personnel', note: '' }],
    next_steps: [{ id: 'evacuate_per_plan', reason: '' }],
  }
  it('合格：编号换成清单里的文字', () => {
    const r = checkExplanation(good, OPTIONS, MATERIAL)
    assert.ok(r.ok)
    assert.equal(r.explanation.focus[1]!.label, OPTIONS.focus_options[2]!.label)
  })
  it('编号不在清单、重复、条数和字数超限、编造数值都判失败', () => {
    const r = checkExplanation(
      {
        summary: '煤温约 150 ℃'.padEnd(70, '。'),
        focus: [{ id: 'level', note: '' }, { id: 'level', note: '' }, { id: 'nope', note: '约 1600 ppm' }],
        next_steps: [],
      },
      OPTIONS,
      MATERIAL,
    )
    assert.ok(!r.ok)
    const all = r.problems.join('\n')
    for (const want of ['关注点应选 1–2 项', '重复：level', '不在清单里：nope', '1600', '下一步应选 1–2 项', '总述超过 60 字', '150']) assert.match(all, new RegExp(want))
  })
  it('不满足撤人提醒条件（如橙色、不建议升级）时，总述、说明、理由里写撤人都判失败；字段缺失按不满足', () => {
    const orange = { ...OPTIONS, next_step_options: [OPTIONS.next_step_options[1]!], evacuation_reminder: false }
    const r = checkExplanation(
      {
        summary: '橙色预警，受威胁 11 人，撤人按预案立即执行',
        focus: [{ id: 'personnel', note: '受威胁人员需撤离' }],
        next_steps: [{ id: 'escalate_emergency', reason: '人员尽快撤出' }],
      },
      orange,
      MATERIAL,
    )
    assert.ok(!r.ok)
    const all = r.problems.join('\n')
    for (const want of ['总述里有撤人提醒', '关注点 personnel 的说明里有撤人提醒', '下一步 escalate_emergency 的说明里有撤人提醒']) assert.match(all, new RegExp(want))
    const { evacuation_reminder: _, ...missing } = orange
    assert.ok(!checkExplanation({ summary: '橙色预警，撤人按预案执行', focus: [{ id: 'level', note: '' }], next_steps: [{ id: 'escalate_emergency', reason: '' }] }, missing, MATERIAL).ok)
    assert.ok(checkExplanation({ summary: '橙色预警，受威胁 11 人，加密监测', focus: [{ id: 'level', note: '' }], next_steps: [{ id: 'escalate_emergency', reason: '' }] }, orange, MATERIAL).ok)
  })
  it('提示词：不满足撤人提醒条件时写明不要写撤人，满足时不写这一条', () => {
    const args = { skillName: 'fire-assessment', skillVersion: '1', skillBody: '', facts: '' }
    assert.match(stagePrompt({ ...args, options: { ...OPTIONS, evacuation_reminder: false } }), /不要写撤人/)
    assert.doesNotMatch(stagePrompt({ ...args, options: OPTIONS }), /不要写撤人/)
  })
  it('结构不完整', () => {
    assert.deepEqual(checkExplanation({ summary: 'x' }, OPTIONS, MATERIAL), { ok: false, problems: ['结构化结果不完整'] })
  })
})

describe('研判说明：提示词和 schema', () => {
  it('schema 的编号只能取清单里的', () => {
    const s: any = stageSchema(OPTIONS)
    assert.deepEqual(s.properties.focus.items.properties.id.enum, ['level', 'explosion:BT-1201-G1', 'personnel'])
    assert.deepEqual(s.required, ['summary', 'focus', 'next_steps'])
  })
  it('提示词含技能正文、事实部分和两份清单', () => {
    const p = stagePrompt({ skillName: 'fire-assessment', skillVersion: '0.1.0', skillBody: '# 技能正文', facts: '【预警研判】', options: OPTIONS })
    for (const want of ['structured_output', '# 技能正文', '【预警研判】', '- personnel：', '- escalate_emergency：']) assert.ok(p.includes(want), want)
  })
})
