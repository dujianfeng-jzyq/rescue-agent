import assert from 'node:assert/strict'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import { defaultFollowup, followupParams, registerTools } from '../src/incident/index.ts'
import { alertStatuses, IncidentStore } from '../src/incident/store.ts'
import { evidenceAsOf } from '../src/incident/timeline.ts'
import { composeMessage, Waker } from '../src/incident/wake.ts'

async function setup() {
  const store = new IncidentStore(await mkdtemp(join(tmpdir(), 'inc-')))
  const id = await store.open('2026-11-05T08:00:00', { category: 'fire', panel: '1201', summary: 'x', by: 'sentinel' })
  const alert = await store.append(id, '2026-11-05T08:10:00', 'alert', 'sentinel', { cause: 'anomaly', summary: '1201 工作面：预警等级由 无 升为 蓝' })
  return { store, id, alert }
}

describe('唤醒', () => {
  it('投递一次后写 delivered，不再重复', async () => {
    const { store, id, alert } = await setup()
    const sent: string[] = []
    const w = new Waker(store, { backoffS: [1], deliver: async (text) => (sent.push(text), { sessionId: 'S1' }) })
    assert.deepEqual(await w.drain(), [alert.event_id])
    assert.deepEqual(await w.drain(), [])
    assert.equal(sent.length, 1)
    assert.match(sent[0]!, new RegExp(`【值守事件 ${id}】`))
    assert.match(sent[0]!, new RegExp(alert.event_id))
    assert.match(sent[0]!, /run_fire_assessment/)
    assert.deepEqual((await store.get(id)).pending_alerts, [])
    const delivered = (await store.entries(id)).find((e) => e.kind === 'delivered')!
    assert.deepEqual(delivered.data, { alert_id: alert.event_id, session_id: 'S1' })
  })

  it('同一事件的多条告警合并成一条消息', async () => {
    const { store, id } = await setup()
    await store.append(id, '2026-11-05T09:00:00', 'alert', 'sentinel', { cause: 'followup_due', summary: '到点' })
    const sent: string[] = []
    const w = new Waker(store, { backoffS: [], deliver: async (t) => (sent.push(t), { sessionId: 'S1' }) })
    assert.equal((await w.drain()).length, 2)
    assert.equal(sent.length, 1)
    assert.match(sent[0]!, /review_effect/)
  })

  it('失败按退避重试，用完放弃；放弃后告警仍是未确认', async () => {
    const { store, id } = await setup()
    let now = Date.parse('2026-11-05T00:00:00Z')
    let fail = true
    let calls = 0
    const w = new Waker(store, {
      backoffS: [10, 60],
      now: () => now,
      deliver: async () => {
        calls += 1
        if (fail) throw new Error('会话落盘失败')
        return { sessionId: 'S1' }
      },
    })
    const stamp = async () => (await store.entries(id)).filter((e) => e.kind === 'delivery_failed').length
    // wall 取系统时间；把“现在”设在其后以便按退避判断
    now = Date.now()
    await w.drain()
    assert.equal(calls, 1)
    assert.equal(await stamp(), 1)
    await w.drain() // 退避内：不重试
    assert.equal(calls, 1)
    now += 11_000
    await w.drain()
    assert.equal(calls, 2)
    now += 61_000
    await w.drain()
    assert.equal(calls, 3)
    now += 10 * 3600_000
    await w.drain() // 已放弃
    assert.equal(calls, 3)
    assert.equal((await store.get(id)).pending_alerts.length, 1)
    // 放弃之后不会自动恢复，但新告警仍会投递
    fail = false
    await store.append(id, '2026-11-05T12:00:00', 'alert', 'sentinel', { cause: 'anomaly', summary: '新的' })
    await w.drain()
    assert.equal(calls, 4)
  })

  it('值班会话忙时不投递；空闲后已被处理覆盖的告警写 acked、不再投递，只投新的', async () => {
    const { store, id, alert } = await setup()
    let busy = true
    const sent: string[] = []
    const w = new Waker(store, { backoffS: [], busy: async () => busy, deliver: async (t) => (sent.push(t), { sessionId: 'S1' }) })
    assert.deepEqual(await w.drain(), [])
    assert.equal(sent.length, 0)
    assert.deepEqual((await store.get(id)).pending_alerts, [alert.event_id])
    // 忙的这一轮用 08:10 的数据研判（覆盖了告警），同时又来一条 09:00 的升级
    await store.append(id, '2026-11-05T08:30:00', 'note', 'flow', { type: 'assessment', text: '研判', as_of: '2026-11-05T08:10:00' })
    const b = await store.append(id, '2026-11-05T09:00:00', 'alert', 'sentinel', { cause: 'anomaly', summary: '升为橙色' })
    busy = false
    assert.deepEqual(await w.drain(), [b.event_id])
    assert.equal(sent.length, 1)
    assert.doesNotMatch(sent[0]!, new RegExp(alert.event_id))
    assert.doesNotMatch(sent[0]!, /重复投递：/)
    assert.match(sent[0]!, /请立即对该工作面做完整研判/)
    const acked = (await store.entries(id)).filter((e) => e.kind === 'acked')
    assert.deepEqual(acked.map((e) => [e.by, e.data.alert_id, e.data.handled_by]), [['plugin', alert.event_id, '研判']])
    assert.deepEqual((await store.get(id)).pending_alerts, [])
    // acked 的依据也出现在 alert_status 里
    assert.equal(alertStatuses(await store.entries(id), new Set([alert.event_id]))[0]!.handled_by, '研判')
  })

  it('全部告警都已被处理覆盖时什么都不投递', async () => {
    const { store, id } = await setup()
    await store.append(id, '2026-11-05T08:30:00', 'note', 'flow', { type: 'assessment', text: '研判', as_of: '2026-11-05T08:20:00' })
    const sent: string[] = []
    const w = new Waker(store, { backoffS: [], busy: async () => false, deliver: async (t) => (sent.push(t), { sessionId: 'S1' }) })
    assert.deepEqual(await w.drain(), [])
    assert.equal(sent.length, 0)
    assert.deepEqual((await store.get(id)).pending_alerts, [])
  })

  it('查不到值班会话状态时照常投递', async () => {
    const { store, alert } = await setup()
    const w = new Waker(store, { backoffS: [], busy: async () => { throw new Error('x') }, deliver: async () => ({ sessionId: 'S1' }) })
    assert.deepEqual(await w.drain(), [alert.event_id])
  })

  it('消息按原因给要求，监听降级不带研判指令', async () => {
    const { store, id } = await setup()
    const entries = await store.entries(id)
    const view = await store.get(id)
    const degraded = { ...entries[1]!, data: { cause: 'degraded', summary: '监听降级' } }
    const text = composeMessage(view, [degraded])
    assert.doesNotMatch(text, /run_fire_assessment/)
    assert.match(text, /不可信/)
  })
})

describe('告警是否已处理（代码判断）与重复投递的唤醒消息', () => {
  it('告警之后有研判记录或确认才算已处理；之后又来的新告警不受影响', async () => {
    const { store, id, alert } = await setup()
    let st = alertStatuses(await store.entries(id))
    assert.deepEqual(st.map((s) => [s.alert_id, s.state, s.handled_by, s.deliveries]), [[alert.event_id, 'new', null, 0]])
    await store.append(id, '2026-11-05T08:11:00', 'delivered', 'plugin', { alert_id: alert.event_id, session_id: 'S1' })
    await store.append(id, '2026-11-05T08:12:00', 'note', 'flow', { type: 'assessment', text: '研判', as_of: '2026-11-05T08:12:00' })
    const b = await store.append(id, '2026-11-05T09:00:00', 'alert', 'sentinel', { cause: 'anomaly', summary: '又一条' })
    st = alertStatuses(await store.entries(id))
    assert.deepEqual(st.map((s) => [s.alert_id, s.state, s.handled_by]), [[alert.event_id, 'handled', '研判'], [b.event_id, 'new', null]])
    assert.equal(st[0]!.deliveries, 1)
    // 确认（acked）也算；只看指定的告警
    await store.append(id, '2026-11-05T09:01:00', 'acked', 'agent', { alert_id: b.event_id })
    const only = alertStatuses(await store.entries(id), new Set([b.event_id]))
    assert.deepEqual(only.map((s) => [s.alert_id, s.state, s.handled_by]), [[b.event_id, 'handled', '确认']])
    // 与告警无关的记录（审批、其他 note）不算处理
    const { store: s2, id: id2, alert: a2 } = await setup()
    await s2.append(id2, '2026-11-05T08:12:00', 'note', 'approval', { type: 'approval_pending', approval_key: 'k', tool: 'publish_warning' })
    assert.equal(alertStatuses(await s2.entries(id2))[0]!.state, 'new')
    assert.ok(a2)
  })

  it('已处理的告警：消息标注“重复投递”，不再带“立即研判”，只让简短说明', async () => {
    const { store, id, alert } = await setup()
    await store.append(id, '2026-11-05T08:11:00', 'delivered', 'plugin', { alert_id: alert.event_id, session_id: 'S1' })
    await store.append(id, '2026-11-05T08:12:00', 'note', 'flow', { type: 'assessment', text: '研判', as_of: '2026-11-05T08:12:00' })
    const entries = await store.entries(id)
    const text = composeMessage(await store.get(id), [entries.find((e) => e.kind === 'alert')!], alertStatuses(entries))
    assert.match(text, /【重复投递：之后已有研判记录，已处理】/)
    assert.match(text, /这是重复投递/)
    assert.doesNotMatch(text, /请立即对该工作面做完整研判/)
    // 新告警的消息不变
    const fresh = composeMessage(await store.get(id), [entries.find((e) => e.kind === 'alert')!], [])
    assert.match(fresh, /请立即对该工作面做完整研判/)
    assert.doesNotMatch(fresh, /重复投递：/)
  })

  it('同一条消息里新旧告警并存：新的照常要求，旧的标注重复', async () => {
    const { store, id, alert } = await setup()
    await store.append(id, '2026-11-05T08:12:00', 'note', 'flow', { type: 'assessment', text: '研判', as_of: '2026-11-05T08:12:00' })
    const b = await store.append(id, '2026-11-05T09:00:00', 'alert', 'sentinel', { cause: 'mine_alarm', summary: '矿井报警' })
    const entries = await store.entries(id)
    const alerts = entries.filter((e) => e.kind === 'alert')
    const text = composeMessage(await store.get(id), alerts, alertStatuses(entries))
    assert.match(text, new RegExp(`${alert.event_id}.*重复投递`))
    assert.doesNotMatch(text, new RegExp(`${b.event_id}.*重复投递`))
    assert.match(text, /矿井自己的报警平台发来的越限报警/) // 新告警（矿井报警）的要求还在
    assert.doesNotMatch(text, /不要先问操作员“要不要研判”/) // 旧告警（异常）的要求没了
  })

  it('依据的数据比告警旧的处理记录不算已处理（上一轮用旧数据写的研判排在新告警之后）', async () => {
    const { store, id, alert } = await setup()
    // 告警 08:10；研判写进时间线的位置在告警之后，但依据的数据是 08:05 的
    await store.append(id, '2026-11-05T08:12:00', 'note', 'flow', { type: 'assessment', text: '旧研判', as_of: '2026-11-05T08:05:00' })
    assert.equal(alertStatuses(await store.entries(id))[0]!.state, 'new')
    // 没有 as_of 的旧条目也不算（fail closed）
    await store.append(id, '2026-11-05T08:13:00', 'note', 'guard', { type: 'action', tool: 'start_monitoring', status: 'issued' })
    assert.equal(alertStatuses(await store.entries(id))[0]!.state, 'new')
    // 数据时间等于告警时间就算覆盖
    await store.append(id, '2026-11-05T08:14:00', 'note', 'guard', { type: 'action', tool: 'publish_warning', status: 'issued', as_of: '2026-11-05T08:10:00' })
    const st = alertStatuses(await store.entries(id))[0]!
    assert.deepEqual([st.alert_id, st.state, st.handled_by], [alert.event_id, 'handled', '对外动作'])
  })

  it('note 的数据时间取引用记录里最早的 as_of，读不到的跳过', async () => {
    const recs: Record<string, { as_of: string }> = { A: { as_of: '2026-11-05T09:00:00' }, B: { as_of: '2026-11-05T08:30:00.5' } }
    const read = async (id: string) => {
      if (!recs[id]) throw new Error('没有')
      return recs[id]
    }
    assert.equal(await evidenceAsOf(['A', 'B', 'X'], read), '2026-11-05T08:30:00')
    assert.equal(await evidenceAsOf(['X'], read), undefined)
  })

  it('get_incident 返回最近告警的处理状态', async () => {
    const { store, id, alert } = await setup()
    await store.append(id, '2026-11-05T08:12:00', 'note', 'flow', { type: 'assessment', text: '研判', as_of: '2026-11-05T08:12:00' })
    const reg: Record<string, any> = {}
    registerTools({ tools: { register: (t: any) => (reg[t.name] = t) } }, store, async () => '2026-11-05T10:00:00', async () => ({ default_after_h: 2, max_after_h: 48 }))
    const out = await reg.get_incident.execute({ incident_id: id })
    assert.deepEqual(out.alert_status.map((s: any) => [s.alert_id, s.state]), [[alert.event_id, 'handled']])
  })
})

describe('事件工具', () => {
  async function tools() {
    const store = new IncidentStore(await mkdtemp(join(tmpdir(), 'inc-')))
    const reg: Record<string, any> = {}
    registerTools({ tools: { register: (t: any) => (reg[t.name] = t) } }, store, async () => '2026-11-05T10:00:00', async () => ({ default_after_h: 2, max_after_h: 48 }))
    const call = async (name: string, args: Record<string, unknown>) => reg[name].execute(args)
    return { store, call, reg }
  }

  it('开事件、合并、登记跟踪计划（到点时间由代码算）、关闭的前置条件', async () => {
    const { call } = await tools()
    const o: any = await call('open_incident', { panel: '1201', summary: '操作员报告上隅角 CO 偏高' })
    assert.equal(o.opened, true)
    assert.equal(((await call('open_incident', { panel: '1201', summary: 'again' })) as any).opened, false)
    const id = o.incident.incident_id
    const f: any = await call('add_followup', { incident_id: id, after_h: 2.5, expect: '注惰后 CO 回落' })
    assert.equal(f.due_at, '2026-11-05T12:30:00')
    await assert.rejects(call('add_followup', { incident_id: id, after_h: -1, expect: 'x' }), /after_h/)
    await assert.rejects(call('add_followup', { incident_id: id, after_h: 49, expect: 'x' }), /不超过 48/)
    const d: any = await call('add_followup', { incident_id: id, expect: '默认间隔' })
    assert.equal(d.due_at, '2026-11-05T12:00:00')
    assert.deepEqual(d.superseded, [f.followup_id], '一个事件只保留一条跟踪：新跟踪取代没完成的旧跟踪')
    const v: any = await call('get_incident', { incident_id: id })
    assert.deepEqual(v.followups.filter((x: any) => x.state !== 'done').map((x: any) => x.followup_id), [d.followup_id])
    await assert.rejects(call('close_incident', { incident_id: id, reason: 'x' }), /未完成的跟踪计划/)
    await call('complete_followup', { incident_id: id, followup_id: d.followup_id, result: 'CO 已回落' })
    await assert.rejects(call('complete_followup', { incident_id: id, followup_id: 'FU-none', result: 'x' }), /没有跟踪计划/)
    await assert.rejects(call('set_incident_status', { incident_id: id, status: 'closed', reason: 'x' }), /status/)
    const closed: any = await call('close_incident', { incident_id: id, reason: '稳定' })
    assert.equal(closed.status, 'closed')
    const list: any = await call('get_incident', {})
    assert.equal(list.count, 0)
  })

  it('add_followup 不给间隔：复评之后没有新处置就按复评给出的下次复评时刻，有新处置按默认间隔', async () => {
    const { store, call } = await tools()
    const id = await store.open('2026-11-05T08:00:00', { category: 'fire', panel: '1201', summary: 'x' })
    const first: any = await call('add_followup', { incident_id: id, expect: '还没复评' })
    assert.equal(first.due_at, '2026-11-05T12:00:00')
    assert.match(first.basis, /还没有复评/)
    await store.append(id, '2026-11-05T09:00:00', 'note', 'flow', { type: 'effect_review', text: '需继续处置', next_review_at: '2026-11-05T21:00:00' })
    const paced: any = await call('add_followup', { incident_id: id, expect: '按复评节奏' })
    assert.equal(paced.due_at, '2026-11-05T21:00:00')
    assert.equal(paced.after_h, 11)
    // 被拒的动作不算新处置
    await store.append(id, '2026-11-05T09:30:00', 'note', 'guard', { type: 'action', tool: 'issue_firefighting_plan', status: 'rejected' })
    assert.equal(((await call('add_followup', { incident_id: id, expect: 'x' })) as any).due_at, '2026-11-05T21:00:00')
    await store.append(id, '2026-11-05T09:40:00', 'note', 'guard', { type: 'action', tool: 'issue_firefighting_plan', status: 'issued' })
    const fresh: any = await call('add_followup', { incident_id: id, expect: '新处置' })
    assert.equal(fresh.due_at, '2026-11-05T12:00:00')
    assert.match(fresh.basis, /新的处置/)
    // 新的复评之后又回到复评节奏；复评时刻已过退回默认；超出上限按上限
    await store.append(id, '2026-11-05T09:50:00', 'note', 'flow', { type: 'effect_review', text: 'x', next_review_at: '2026-11-05T09:55:00' })
    assert.equal(((await call('add_followup', { incident_id: id, expect: 'x' })) as any).due_at, '2026-11-05T12:00:00')
    await store.append(id, '2026-11-05T09:58:00', 'note', 'flow', { type: 'effect_review', text: 'x', next_review_at: '2026-11-09T10:00:00' })
    assert.equal(((await call('add_followup', { incident_id: id, expect: 'x' })) as any).due_at, '2026-11-07T10:00:00')
    // 给了 after_h 就按给的
    assert.equal(((await call('add_followup', { incident_id: id, after_h: 1, expect: 'x' })) as any).due_at, '2026-11-05T11:00:00')
  })

  it('发布预警、升级、加密监测不算新的处置；撤人、调度、方案算（2026-11-11）', async () => {
    const { store, call } = await tools()
    const id = await store.open('2026-11-05T08:00:00', { category: 'fire', panel: '1201', summary: 'x' })
    await store.append(id, '2026-11-05T09:00:00', 'note', 'flow', { type: 'effect_review', text: 'x', next_review_at: '2026-11-05T21:00:00' })
    for (const tool of ['publish_warning', 'escalate_emergency', 'start_monitoring']) {
      await store.append(id, '2026-11-05T09:10:00', 'note', 'guard', { type: 'action', tool, status: 'issued' })
    }
    const paced: any = await call('add_followup', { incident_id: id, expect: 'x' })
    assert.equal(paced.due_at, '2026-11-05T21:00:00')
    assert.match(paced.basis, /没有新处置/)
    for (const tool of ['notify_evacuation', 'issue_dispatch_order']) {
      const id2 = await store.open('2026-11-05T08:00:00', { category: 'fire', panel: `P-${tool}`, summary: 'x' })
      await store.append(id2, '2026-11-05T09:00:00', 'note', 'flow', { type: 'effect_review', text: 'x', next_review_at: '2026-11-05T21:00:00' })
      await store.append(id2, '2026-11-05T09:10:00', 'note', 'guard', { type: 'action', tool, status: 'issued' })
      assert.match(((await call('add_followup', { incident_id: id2, expect: 'x' })) as any).basis, /新的处置/, tool)
    }
  })

  it('按当前预警等级限复评间隔，取较早的；封闭方案下发后不限；给了 after_h 不限', () => {
    const p = { default_after_h: 2, max_after_h: 48, level_cap_h: { blue: 4, yellow: 4, orange: 2, red: 1 } }
    const now = '2026-11-05T10:00:00'
    const e = (kind: string, by: string, data: Record<string, unknown>, at = '2026-11-05T09:00:00') => ({ event_id: 'x', at, kind, by, data }) as any
    const review = e('note', 'flow', { type: 'effect_review', next_review_at: '2026-11-05T22:00:00', overall: 'worsening' })
    // 橙色、复评说 12 小时后：限到 2 小时
    const orange = defaultFollowup([e('note', 'flow', { type: 'assessment', level: 'orange' }), review], now, p)
    assert.equal(orange.due_at, '2026-11-05T12:00:00')
    assert.match(orange.basis, /按复评给出的下次复评时刻 2026-11-05T22:00:00；当前预警等级 橙色（严重），复评间隔不超过 2 小时，按 2 小时$/)
    // 等级取最近一次研判；黄色上限 4 小时
    assert.equal(defaultFollowup([e('note', 'flow', { type: 'assessment', level: 'orange' }), e('note', 'flow', { type: 'assessment', level: 'yellow' }), review], now, p).due_at, '2026-11-05T14:00:00')
    // 红色封闭前：默认 2 小时也限到 1 小时
    assert.equal(defaultFollowup([e('note', 'flow', { type: 'assessment', level: 'red' })], now, p).due_at, '2026-11-05T11:00:00')
    // 封闭方案下发后：不按等级限
    const sealed = defaultFollowup([e('note', 'flow', { type: 'assessment', level: 'red' }), e('note', 'guard', { type: 'action', tool: 'issue_sealing_plan', status: 'issued' }, '2026-11-05T09:30:00'), e('note', 'flow', { type: 'effect_review', next_review_at: '2026-11-05T22:00:00' }, '2026-11-05T09:40:00')], now, p)
    assert.equal(sealed.due_at, '2026-11-05T22:00:00')
    // 没有等级（旧时间线）或没有配置：不限
    assert.equal(defaultFollowup([review], now, p).due_at, '2026-11-05T22:00:00')
    assert.equal(defaultFollowup([e('note', 'flow', { type: 'assessment', level: 'orange' }), review], now, { default_after_h: 2, max_after_h: 48 }).due_at, '2026-11-05T22:00:00')
  })

  it('有未确认告警不能关闭；get_incident 给出时间线', async () => {
    const { store, call } = await tools()
    const id = await store.open('2026-11-05T08:00:00', { category: 'fire', panel: '1201', summary: 'x' })
    const a = await store.append(id, '2026-11-05T08:10:00', 'alert', 'sentinel', { cause: 'anomaly', summary: 's' })
    await assert.rejects(call('close_incident', { incident_id: id, reason: 'x' }), /未确认的告警/)
    const v: any = await call('get_incident', { incident_id: id })
    assert.deepEqual(v.pending_alerts, [a.event_id])
    assert.equal(v.pending_alert_details[0].summary, 's')
    assert.equal(v.timeline.length, 2)
  })
})

describe('跟踪参数文件', () => {
  it('followup.yaml 的等级上限能读出来（占位值）', async () => {
    const p = await followupParams(join(import.meta.dirname, '..', '..', 'skills'))
    assert.deepEqual(Object.keys(p.level_cap_h ?? {}).sort(), ['blue', 'orange', 'red', 'yellow'])
    assert.ok(Object.values(p.level_cap_h!).every((h) => h > 0 && h <= p.max_after_h))
    // 等级越高，复评间隔上限不能越长
    const c = p.level_cap_h!
    assert.ok(c.blue! >= c.yellow! && c.yellow! >= c.orange! && c.orange! >= c.red!, JSON.stringify(c))
  })
})
