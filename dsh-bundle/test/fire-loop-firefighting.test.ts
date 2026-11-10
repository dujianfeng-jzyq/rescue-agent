import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, it } from 'node:test'

import { FIREFIGHTING, SEALING } from '../src/fire-loop/sealing.ts'
import { checkSelection, defaultSelection, reviewPrompt, type Scope, type Skeleton, skeletonText, writePrompt, writeSchema } from '../src/fire-loop/plan-stages.ts'

// 骨架、审核范围取自防灭火方案的技能样例（s3-accelerating 第 39 小时，橙色）
const skills = new URL('../../skills/', import.meta.url)
const read = (p: string) => JSON.parse(readFileSync(new URL(p, skills), 'utf8')).result
const SK: Skeleton = read('firefighting-plan/tests/cases/skeleton/s3-accelerating-39h.expected.json')
const SC: Scope = read('plan-compliance/tests/cases/scope/firefighting-s3-39h-full.expected.json')
const MATERIAL = skeletonText(SK) + JSON.stringify(SK)

describe('防灭火方案：流程配置', () => {
  it('不算受威胁人员，下发工具与封闭方案不同', () => {
    assert.equal(FIREFIGHTING.withPersonnel, false)
    assert.equal(SEALING.withPersonnel, true)
    assert.equal(FIREFIGHTING.issueTool, 'issue_firefighting_plan')
    assert.notEqual(FIREFIGHTING.skill, SEALING.skill)
  })
})

describe('防灭火方案：方案编写', () => {
  const good = { optional_steps: ['leak_sealing', 'monitor_after'], step_notes: [{ key: 'inert_injection', note: '注氮口位置按现场确定' }, { key: 'monitor', note: '' }], summary: '先注惰降氧，不封闭火区' }
  it('合格：没有密闭巷道，空说明去掉', () => {
    const r = checkSelection(good, SK, MATERIAL)
    assert.ok(r.ok)
    assert.deepEqual(r.selection.seal_roadways, [])
    assert.deepEqual(r.selection.step_notes, [{ key: 'inert_injection', note: '注氮口位置按现场确定' }])
  })
  it('不能选密闭巷道、可选步骤不在清单里', () => {
    const r = checkSelection({ ...good, seal_roadways: ['R-1201-IN'], optional_steps: ['permanent_seal'] }, SK, MATERIAL)
    assert.ok(!r.ok)
    assert.ok(r.problems.includes('本方案不封闭火区，不能选密闭巷道'))
    assert.ok(r.problems.some((p) => p.includes('permanent_seal')))
  })
  it('schema 没有密闭巷道；缺省选择不加任何步骤', () => {
    const s: any = writeSchema(SK)
    assert.equal(s.properties.seal_roadways, undefined)
    assert.ok(!s.required.includes('seal_roadways'))
    assert.ok(s.properties.optional_steps.items.enum.includes('grouting'))
    assert.deepEqual(defaultSelection(SK), { optional_steps: [], seal_roadways: [], step_notes: [], summary: '' })
  })
  it('骨架文字不含封闭方案的内容，提示词用本流程的名字', () => {
    const text = skeletonText(SK)
    assert.ok(!text.includes('密闭巷道清单') && !text.includes('撤人巷道') && !text.includes('灾区温度'))
    assert.ok(text.includes('不封闭火区') && text.includes('inert_injection'))
    const p = writePrompt({ skillName: 'firefighting-plan', skillVersion: '0.1.0', skillBody: '# 防灭火方案', skeleton: SK, comments: [], flow: FIREFIGHTING.name })
    assert.ok(p.includes('draft_firefighting_plan') && !p.includes('seal_roadways：') && p.includes('# 防灭火方案'))
  })
})

describe('防灭火方案：操作员选定的步骤', () => {
  it('写作提示词要求正好是选定的步骤；没有选定时照常由模型选', () => {
    const base = { skillName: 'firefighting-plan', skillVersion: '0.1.0', skillBody: '# 防灭火方案', skeleton: SK, comments: [], flow: FIREFIGHTING.name }
    assert.ok(writePrompt({ ...base, forcedOptionalSteps: ['grouting'] }).includes('操作员已选定，必须正好是 [grouting]'))
    assert.ok(writePrompt(base).includes('从“可选步骤清单”里选需要的'))
  })
})

describe('防灭火方案：合规审核范围', () => {
  it('审核项是防灭火方案专用的四项', () => {
    assert.deepEqual(SC.items.map((i) => i.id), ['basis', 'context', 'no_entry', 'ventilation'])
    assert.ok(SC.articles.some((a) => a.article === '第98条') && SC.articles.some((a) => a.article === '第46条'))
  })
  it('提示词用本流程的名字', () => {
    const p = reviewPrompt({ skillName: 'plan-compliance', skillVersion: '0.1.0', skillBody: '# 审核', scope: SC, planText: '方案', redline: { ok: true, problems: [] }, articles: [], flow: FIREFIGHTING.name })
    assert.ok(p.includes('draft_firefighting_plan') && p.includes('no_entry'))
  })
})
