import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { AgentActivity, toolLabel } from '../src/map/activity.ts'

function clock() {
  let t = 1_000_000
  return { now: () => t, advance: (ms: number) => void (t += ms) }
}

describe('AgentActivity', () => {
  it('没收到过任何事件时 known 为 false（界面不能说“实时”），收到后为 true', () => {
    const a = new AgentActivity()
    assert.equal(a.snapshot().known, false)
    a.status('agent-1', 'idle')
    assert.equal(a.snapshot().known, true, '收到过 idle 事件也算收到过事件')
    assert.equal(a.snapshot().running, 0)
  })

  it('running 计数，idle 清掉该智能体和它名下的工具', () => {
    const a = new AgentActivity()
    a.status('root', 'running')
    a.status('child', 'running')
    a.toolStart('c1', 'root', 'run_fire_assessment')
    assert.equal(a.snapshot().running, 2)
    assert.deepEqual(a.snapshot().doing, ['研判'])
    a.status('root', 'idle')
    assert.equal(a.snapshot().running, 1)
    assert.deepEqual(a.snapshot().doing, [], 'idle 后工具调用一并清掉（被拒绝的工具没有 post-execute）')
  })

  it('工具开始、结束；不认识的工具不显示具体动作；同一个动作去重', () => {
    const a = new AgentActivity()
    a.status('root', 'running')
    a.toolStart('c1', 'root', 'run_fire_assessment')
    a.toolStart('c2', 'root', 'run_fire_assessment')
    a.toolStart('c3', 'root', 'some_unknown_tool')
    assert.deepEqual(a.snapshot().doing, ['研判'])
    a.toolEnd('c1', 'root')
    a.toolEnd('c2', 'root')
    assert.deepEqual(a.snapshot().doing, [])
    assert.equal(a.snapshot().running, 1, '工具结束不等于智能体闲了')
  })

  it('等待人工确认、等待回答：开始和结束', () => {
    const a = new AgentActivity()
    a.approvalStart('x', 'mcp__actions__publish_warning')
    a.questionStart('q')
    assert.deepEqual(a.snapshot().waitingApprovals, [{ tool: 'publish_warning', name: '发布预警等级' }])
    assert.equal(a.snapshot().waitingQuestions, 1)
    a.approvalEnd('x')
    a.questionEnd('q')
    assert.equal(a.snapshot().waitingApprovals.length + a.snapshot().waitingQuestions, 0)
  })

  it('过期保护：漏了 idle 事件时，15 分钟没动静的记录当作没有', () => {
    const c = clock()
    const a = new AgentActivity(c.now)
    a.status('root', 'running')
    a.questionStart('q')
    c.advance(14 * 60_000)
    assert.equal(a.snapshot().running, 1)
    c.advance(2 * 60_000)
    assert.equal(a.snapshot().running, 0)
    assert.equal(a.snapshot().waitingQuestions, 0)
    assert.equal(a.snapshot().known, true, '过期不等于没收到过事件')
  })

  it('有新事件就刷新过期时间', () => {
    const c = clock()
    const a = new AgentActivity(c.now)
    a.status('root', 'running')
    c.advance(10 * 60_000)
    a.toolStart('c1', 'root', 'check_mine')
    c.advance(10 * 60_000)
    assert.equal(a.snapshot().running, 1)
  })

  it('agent 被销毁时清掉', () => {
    const a = new AgentActivity()
    a.status('root', 'running')
    a.dispose('root')
    assert.equal(a.snapshot().running, 0)
  })
})

describe('toolLabel', () => {
  it('固定流程、事件工具有中文说明；对外动作取动作分级表的名称；不认识的为 null', () => {
    assert.equal(toolLabel('draft_sealing_plan'), '编制封闭方案')
    assert.equal(toolLabel('mcp__actions__issue_firefighting_plan'), '提交：下发防灭火方案')
    assert.equal(toolLabel('mcp__mine_data__get_latest'), null)
    assert.equal(toolLabel('bash'), null)
  })
})
