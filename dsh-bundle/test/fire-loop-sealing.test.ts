import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, it } from 'node:test'

import { checkReview, checkSelection, defaultSelection, reviewSchema, type Scope, type Skeleton, skeletonText, writePrompt, writeSchema } from '../src/fire-loop/plan-stages.ts'

// 骨架、审核范围取自技能样例（s4-escalation 第 60 小时）
const skills = new URL('../../skills/', import.meta.url)
const read = (p: string) => JSON.parse(readFileSync(new URL(p, skills), 'utf8')).result
const SK: Skeleton = read('sealing-plan/tests/cases/skeleton/s4-escalation-60h.expected.json')
const SC: Scope = read('plan-compliance/tests/cases/scope/s4-escalation-60h-full.expected.json')
const MATERIAL = skeletonText(SK) + JSON.stringify(SK)

describe('方案编写：选择核对', () => {
  const good = { optional_steps: ['monitor_after_seal'], seal_roadways: ['R-1201-IN', 'R-1201-RET'], step_notes: [{ key: 'temporary_seal', note: '进、回风同时封闭，统一指挥' }, { key: 'monitor', note: '' }], summary: '先注惰后封闭' }
  it('合格：空说明去掉', () => {
    const r = checkSelection(good, SK, MATERIAL)
    assert.ok(r.ok)
    assert.deepEqual(r.selection.step_notes, [{ key: 'temporary_seal', note: '进、回风同时封闭，统一指挥' }])
  })
  it('编号不在清单、重复、没选密闭巷道、说明的步骤不在方案里', () => {
    const r = checkSelection({ ...good, optional_steps: ['reverse_ventilation', 'monitor_after_seal', 'monitor_after_seal'], seal_roadways: [], step_notes: [{ key: 'grouting', note: '' }] }, SK, MATERIAL)
    assert.ok(!r.ok)
    assert.ok(r.problems.some((p) => p.includes('reverse_ventilation')))
    assert.ok(r.problems.includes('可选步骤有重复') && r.problems.includes('至少选一条密闭巷道'))
    assert.ok(r.problems.some((p) => p.includes('不在方案里：grouting')))
  })
  it('说明里有材料中找不到的数值、超长', () => {
    const r = checkSelection({ ...good, summary: '注氮约 800 m³/h' + '很长'.repeat(60) }, SK, MATERIAL)
    assert.ok(!r.ok && r.problems.some((p) => p.includes('800')) && r.problems.some((p) => p.includes('超过')))
  })
  it('现场条件里的数值可以照抄', () => {
    const r = checkSelection({ ...good, summary: `氧浓度 ${SK.context.o2_pct} %，先注惰` }, SK, MATERIAL)
    assert.ok(r.ok)
  })
  it('schema 的编号来自清单；缺省选择封闭全部候选巷道', () => {
    const s: any = writeSchema(SK)
    assert.deepEqual(s.properties.seal_roadways.items.enum, ['R-1201-IN', 'R-1201-RET'])
    assert.ok(s.properties.optional_steps.items.enum.includes('grouting'))
    assert.deepEqual(defaultSelection(SK).seal_roadways, ['R-1201-IN', 'R-1201-RET'])
  })
  it('提示词带技能正文、骨架和上一轮意见', () => {
    const p = writePrompt({ skillName: 'sealing-plan', skillVersion: '0.1.0', skillBody: '# 封闭方案', skeleton: SK, comments: ['现场侦察：需写明'] })
    assert.ok(p.includes('# 封闭方案') && p.includes('R-1201-RET') && p.includes('上一轮合规审核的意见') && p.includes('现场侦察：需写明'))
  })
})

describe('合规审核：选择核对', () => {
  const all = SC.items.map((i) => ({ id: i.id, conclusion: 'pass', articles: [] as string[], note: '' }))
  it('合格', () => {
    assert.ok(checkReview({ items: all, summary: '通过' }, SC, '方案').ok)
  })
  it('漏项、未给定的条文、结论不在清单', () => {
    const items = [{ ...all[0]!, articles: ['第97条'] }, { ...all[1]!, conclusion: 'maybe' }]
    const r = checkReview({ items, summary: '' }, SC, '方案')
    assert.ok(!r.ok)
    assert.ok(r.problems.some((p) => p.includes('第97条')))
    assert.ok(r.problems.some((p) => p.includes('maybe')))
    assert.ok(r.problems.some((p) => p.includes('没有逐项给出结论')))
  })
  it('条号的并列写法不算数值', () => {
    const items = all.map((x) => (x.id === 'basis' ? { ...x, note: '步骤引用的第84、85、86条与条文一致' } : x))
    assert.ok(checkReview({ items, summary: '' }, SC, '方案').ok)
  })
  it('schema 只用 DSH 结构化输出支持的关键字', () => {
    const allowed = new Set(['type', 'oneOf', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'const', 'description', 'title'])
    const walk = (n: any): void => {
      if (!n || typeof n !== 'object') return
      for (const [k, v] of Object.entries(n)) {
        if (k === 'properties') Object.values(v as object).forEach(walk)
        else {
          assert.ok(allowed.has(k), `不支持的关键字 ${k}`)
          if (k === 'items') walk(v)
        }
      }
    }
    walk(writeSchema(SK))
    walk(reviewSchema(SC))
  })
  it('schema 的条号只来自给定范围', () => {
    const s: any = reviewSchema(SC)
    assert.deepEqual(s.properties.items.items.properties.articles.items.enum, SC.articles.map((a) => a.article))
  })
})
