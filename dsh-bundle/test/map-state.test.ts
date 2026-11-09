import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import { IncidentStore } from '../src/incident/store.ts'
import { buildState, timelineOf } from '../src/map/state.ts'
import type { MineCaller } from '../src/map/state.ts'

const SENSORS = [
  { id: 'S1', name: '上隅角 CO', role: 'upper_corner', panel: '211016', measurands: ['CO'] },
  { id: 'S2', name: '回风巷风速', role: 'return_airway', panel: '211016', measurands: ['WIND'] },
  { id: 'S3', name: '别的工作面', role: 'upper_corner', panel: '1201', measurands: ['CO'] },
]
const mine = (over: Partial<Record<string, (a: any) => any>> = {}): MineCaller => ({
  async call(tool, args) {
    const h = over[tool]
    if (h) return h(args)
    if (tool === 'get_clock') return { now: '2026-11-09T08:00:00' }
    if (tool === 'list_sensors') return SENSORS
    if (tool === 'get_latest') {
      return (args.sensor_ids as string[]).flatMap((id) => (id === 'S1' ? [{ sensor_id: id, measurand: 'CO', ts: 'x', value: 42, quality: 'ok' }] : [{ sensor_id: id, measurand: 'WIND', ts: 'x', value: null, quality: 'missing' }]))
    }
    throw new Error(`未知工具 ${tool}`)
  },
})

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'map-state-'))
  const incidentDir = join(root, 'inc')
  const recordDir = join(root, 'rec')
  await mkdir(incidentDir, { recursive: true })
  await mkdir(recordDir, { recursive: true })
  return { incidentDir, recordDir, store: new IncidentStore(incidentDir) }
}

describe('buildState', () => {
  it('没有事件时：待命、无事件、读数取自第一个有测点的工作面', async () => {
    const f = await fixture()
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: mine() })
    assert.equal(s.incident, null)
    assert.equal(s.agent.state, 'idle')
    assert.equal(s.mine.now, '2026-11-09T08:00:00')
    assert.equal(s.readings.panel, '211016')
    assert.deepEqual(s.readings.rows.map((r) => [r.sensor, r.value, r.unit]), [['上隅角 CO', 42, 'ppm'], ['回风巷风速', null, 'm/s']])
    assert.equal(s.readings.rows[1]!.quality, 'missing', '缺测如实显示，不补数')
  })

  it('矿井返回的列表被包了一层 {result: [...]} 时照常解开', async () => {
    const f = await fixture()
    const wrapped: MineCaller = { async call(tool, args) { return { result: await mine().call(tool, args) } } }
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: wrapped })
    assert.equal(s.readings.rows.length, 2)
  })

  it('等级和阶段取自监听器写进告警的检测摘要，不在这里重算', async () => {
    const f = await fixture()
    const id = await f.store.open('2026-11-09T07:00:00', { category: 'fire', panel: '211016', summary: '211016 工作面 CO 升高', by: 'sentinel' })
    await f.store.append(id, '2026-11-09T07:10:00', 'alert', 'sentinel', { cause: 'level_up', summary: '预警升为橙色', check: { as_of: '2026-11-09T07:10:00', level: 'orange', level_name: '橙色', stage: 'accelerating', stage_name: '加速氧化', alarm_count: 3 } })
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: mine() })
    assert.equal(s.incident!.level, 'orange')
    assert.equal(s.incident!.levelName, '橙色')
    assert.equal(s.incident!.stageName, '加速氧化')
    assert.equal(s.incident!.alarmCount, 3)
    assert.equal(s.incident!.pendingAlerts, 1)
    assert.equal(s.agent.state, 'alert_pending')
    assert.equal(s.readings.panel, '211016', '读数的工作面跟随事件')
  })

  it('有待人工确认的指令时，智能体状态是“等待人工确认”，并带出待确认项', async () => {
    const f = await fixture()
    const id = await f.store.open('2026-11-09T07:00:00', { category: 'fire', panel: '211016', summary: 's' })
    await f.store.append(id, '2026-11-09T07:20:00', 'note', 'plugin', { type: 'approval_pending', approval_key: 'k1', tool: 'publish_warning', text: '发布预警等待确认' })
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: mine() })
    assert.equal(s.agent.state, 'waiting_approval')
    assert.equal(s.incident!.pendingApprovals[0]!.tool, 'publish_warning')
    assert.equal(s.incident!.pendingApprovals[0]!.name, '发布预警等级', '中文名取自动作分级表')
    assert.equal(s.incident!.pendingApprovals[0]!.confirmer, '通风部门负责人')
    await f.store.append(id, '2026-11-09T07:25:00', 'note', 'plugin', { type: 'approval_decided', approval_key: 'k1', decision: 'allow' })
    const s2 = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: mine() })
    assert.equal(s2.incident!.pendingApprovals.length, 0)
  })

  it('矿井服务不可用：时间和读数如实报不可用，其余部分照常', async () => {
    const f = await fixture()
    const down = mine({ get_clock: () => { throw new Error('连接失败') }, list_sensors: () => { throw new Error('连接失败') } })
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: down })
    assert.equal(s.mine.ok, false)
    assert.match(s.mine.error ?? '', /连接失败/)
    assert.deepEqual(s.readings.rows, [])
    assert.match(s.readings.error ?? '', /读数取不到/)
    const none = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null })
    assert.match(none.readings.error ?? '', /没有配置/)
  })

  it('监听器心跳：给出多久没更新和错误', async () => {
    const f = await fixture()
    await mkdir(join(f.incidentDir, 'sentinel'), { recursive: true })
    await writeFile(join(f.incidentDir, 'sentinel', 'heartbeat.json'), JSON.stringify({ wall: 1000, error: '取数失败' }))
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null, nowMs: () => 1_030_000 })
    assert.deepEqual(s.sentinel, { known: true, ageS: 30, error: '取数失败' })
    const empty = await fixture()
    assert.equal((await buildState({ incidentDir: empty.incidentDir, recordDir: empty.recordDir, mine: null })).sentinel.known, false)
  })

  it('记录：只列合规文件名，最新的在前，坏文件跳过', async () => {
    const f = await fixture()
    const rec = (id: string, kind: string) => JSON.stringify({ record_id: id, kind, panel: '211016', as_of: '2026-11-09T07:00:00', created_at: '2026-11-09T07:00:05' })
    await writeFile(join(f.recordDir, 'RS-20261109T070000-aaaaaa.json'), rec('RS-20261109T070000-aaaaaa', 'result'))
    await new Promise((r) => setTimeout(r, 15))
    await writeFile(join(f.recordDir, 'RP-20261109T071000-bbbbbb.json'), rec('RP-20261109T071000-bbbbbb', 'report'))
    await writeFile(join(f.recordDir, 'EV-20261109T071500-cccccc.json'), '{坏的')
    await writeFile(join(f.recordDir, 'notes.json'), '{}')
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null })
    assert.deepEqual(s.records.map((r) => r.id), ['RP-20261109T071000-bbbbbb', 'RS-20261109T070000-aaaaaa'])
    assert.equal(s.records[0]!.kindName, '报告')
  })
})

describe('智能体状态（实时活动优先，没有事件才由事件库推断）', () => {
  const act = (over: Record<string, unknown> = {}) => ({ known: true, running: 0, doing: [] as string[], waitingQuestions: 0, waitingApprovals: [] as { tool: string; name: string }[], ...over })

  it('在忙：显示正在做什么；闲：待命，即使事件还是“处置中”', async () => {
    const f = await fixture()
    const id = await f.store.open('2026-11-09T07:00:00', { category: 'fire', panel: '211016', summary: 's' })
    await f.store.append(id, '2026-11-09T07:05:00', 'status', 'agent', { status: 'handling', reason: 'x' })
    const busy = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null, activity: act({ running: 2, doing: ['研判'] }) })
    assert.equal(busy.agent.state, 'working')
    assert.equal(busy.agent.label, '工作中 · 研判')
    assert.equal(busy.agent.source, 'live')
    const idle = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null, activity: act() })
    assert.equal(idle.agent.state, 'idle', '实时事件说闲，就不再用“事件处置中”推断成工作中')
  })

  it('等待人工确认优先；等待回答次之；都比“在忙”优先', async () => {
    const f = await fixture()
    const s1 = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null, activity: act({ running: 1, waitingApprovals: [{ tool: 'publish_warning', name: '发布预警等级' }] }) })
    assert.equal(s1.agent.state, 'waiting_approval')
    assert.match(s1.agent.label, /等待人工确认（1 项）/)
    const s2 = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null, activity: act({ running: 1, waitingQuestions: 1 }) })
    assert.equal(s2.agent.state, 'waiting_question')
  })

  it('实时的待确认立刻出现，不用等事件库里的记录（界面少一次轮询延迟）', async () => {
    const f = await fixture()
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null, activity: act({ waitingApprovals: [{ tool: 'issue_sealing_plan', name: '下发封闭方案' }] }) })
    assert.equal(s.agent.state, 'waiting_approval')
    assert.equal(s.incident, null)
  })

  it('没收到过事件：标明由事件库推断（source=inferred），处置中的事件推断为处置中', async () => {
    const f = await fixture()
    const id = await f.store.open('2026-11-09T07:00:00', { category: 'fire', panel: '211016', summary: 's' })
    await f.store.append(id, '2026-11-09T07:05:00', 'status', 'agent', { status: 'handling', reason: 'x' })
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null, activity: act({ known: false }) })
    assert.equal(s.agent.source, 'inferred')
    assert.equal(s.agent.state, 'working')
    assert.equal(s.agent.label, '处置中')
    assert.equal((await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null })).agent.source, 'inferred')
  })

  it('智能体闲着但有告警没处理：提示有告警待处理（可能唤醒失败）', async () => {
    const f = await fixture()
    const id = await f.store.open('2026-11-09T07:00:00', { category: 'fire', panel: '211016', summary: 's' })
    await f.store.append(id, '2026-11-09T07:10:00', 'alert', 'sentinel', { cause: 'c', summary: 's' })
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null, activity: act() })
    assert.equal(s.agent.state, 'alert_pending')
  })
})

describe('撤人路线（来自 personnel 记录）', () => {
  const CASES = new URL('../../skills/fire-assessment/tests/cases/personnel/', import.meta.url)
  const read = (n: string) => JSON.parse(readFileSync(new URL(n, CASES), 'utf8'))
  const NETWORK = read('s4-escalation-60h-red.input.json').evidence.network
  const RESULT = read('s4-escalation-60h-red.expected.json').result
  const POS = { N0: [100, 700], N1: [200, 600], N2: [400, 300], N3: [700, 300], N4: [760, 400], N5: [500, 420], N6: [300, 440], N7: [100, 500], N8: [420, 260], N9: [250, 580] }

  async function setup(withRecord = true) {
    const f = await fixture()
    const id = await f.store.open('2026-11-04T20:00:00', { category: 'fire', panel: '1201', summary: 's' })
    if (withRecord) {
      const rec = { record_id: 'RS-20261104T200000-aaaaaa', kind: 'result', panel: '1201', as_of: '2026-11-04T20:00:00', source: { skill: 'fire-assessment', script: 'personnel' }, content: { result: RESULT } }
      await writeFile(join(f.recordDir, 'RS-20261104T200000-aaaaaa.json'), JSON.stringify(rec))
    }
    return { ...f, id }
  }
  const map = { panels: {}, roadways: {}, sensors: {}, nodes: POS, via: {}, status: 'placeholder', reviewedBy: null } as never
  const mineAt = (now: string) => mine({ get_clock: () => ({ now }) })

  it('该工作面有 personnel 记录时给出撤人信息：合计、分组、路线文字，并按映射生成图元', async () => {
    const f = await setup()
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: mineAt('2026-11-04T20:30:00'), map, network: async () => NETWORK })
    const e = s.evacuation!
    assert.equal(e.totalThreatened, 11)
    assert.equal(e.groups[0]!.route.walkMin, 32.7)
    assert.ok(e.figures.some((x) => x.id === 'auto:evac:route:R-1201-FACE'))
    assert.equal(e.freshness, 'fresh')
  })

  it('取不到矿井网络：不画图元，但分组和路线文字照常给出', async () => {
    const f = await setup()
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: mineAt('2026-11-04T20:30:00'), map, network: async () => null })
    assert.deepEqual(s.evacuation!.figures, [])
    assert.equal(s.evacuation!.groups.length, 2)
  })

  it('没有记录、没有事件：evacuation 为 null', async () => {
    const f = await setup(false)
    assert.equal((await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null })).evacuation, null)
    const f2 = await fixture()
    assert.equal((await buildState({ incidentDir: f2.incidentDir, recordDir: f2.recordDir, mine: null })).evacuation, null)
  })

  it('记录过了 6 小时：不再出图元，但列表保留并标“hidden”', async () => {
    const f = await setup()
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: mineAt('2026-11-05T03:00:00'), map, network: async () => NETWORK })
    assert.equal(s.evacuation!.freshness, 'hidden')
    assert.deepEqual(s.evacuation!.figures, [])
    assert.equal(s.evacuation!.groups.length, 2)
  })
})

describe('选当前事件', () => {
  /** 直接写事件文件，系统时间（wall）和矿井时间（at）各自可控 */
  async function writeIncident(dir: string, id: string, rows: { at: string; wall: string; kind: string; data: Record<string, unknown> }[]): Promise<void> {
    const lines = rows.map((r, i) => JSON.stringify({ event_id: `EVT-${id}-${i}`, at: r.at, wall: r.wall, kind: r.kind, by: 'x', data: r.data }))
    await writeFile(join(dir, `${id}.jsonl`), `${lines.join('\n')}\n`)
  }
  const opened = (panel: string) => ({ category: 'fire', panel, summary: panel })

  it('系统时间上最近有动静的优先，不看矿井时间（演示重置后旧事件的矿井时间可能更晚）', async () => {
    const f = await fixture()
    await writeIncident(f.incidentDir, 'INC-20261105T180000-oldold', [{ at: '2026-11-05T18:00:00', wall: '2026-11-03T10:00:00+00:00', kind: 'opened', data: opened('1201') }])
    await writeIncident(f.incidentDir, 'INC-20261104T140000-newnew', [{ at: '2026-11-04T14:00:00', wall: '2026-11-09T10:00:00+00:00', kind: 'opened', data: opened('211016') }])
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null })
    assert.equal(s.incident!.panel, '211016')
    assert.equal(s.openIncidents, 2)
  })

  it('有待人工确认指令的事件优先，哪怕别的事件更新', async () => {
    const f = await fixture()
    await writeIncident(f.incidentDir, 'INC-20261104T140000-aaaaaa', [
      { at: '2026-11-04T14:00:00', wall: '2026-11-09T09:00:00+00:00', kind: 'opened', data: opened('211016') },
      { at: '2026-11-04T14:10:00', wall: '2026-11-09T09:05:00+00:00', kind: 'note', data: { type: 'approval_pending', approval_key: 'k', tool: 'publish_warning' } },
    ])
    await writeIncident(f.incidentDir, 'INC-20261104T150000-bbbbbb', [{ at: '2026-11-04T15:00:00', wall: '2026-11-09T10:00:00+00:00', kind: 'opened', data: opened('1202') }])
    const s = await buildState({ incidentDir: f.incidentDir, recordDir: f.recordDir, mine: null })
    assert.equal(s.incident!.panel, '211016')
    assert.equal(s.agent.state, 'waiting_approval')
  })
})

describe('timelineOf', () => {
  it('人工确认的记录用动作名称、确认人、结果拼出文字', () => {
    const e = (data: Record<string, unknown>) => ({ event_id: 'x', at: '07:05', wall: '', kind: 'note' as const, by: 'approval', data })
    const [decided, pending] = timelineOf([e({ type: 'approval_pending', tool: 'publish_warning' }), e({ type: 'approval_decided', tool: 'publish_warning', outcome: 'rejected' })])
    assert.equal(pending!.text, '发布预警等级（确认人：通风部门负责人）')
    assert.equal(decided!.text, '发布预警等级 · 已拒绝')
  })

  it('最新的在前，隐藏投递细节，研判类记录用中文名', () => {
    const e = (kind: string, at: string, data: any = {}) => ({ event_id: at, at, wall: '', kind: kind as any, by: 'x', data })
    const t = timelineOf([e('opened', '07:00', { summary: '开' }), e('delivered', '07:01'), e('note', '07:05', { type: 'assessment', text: '橙色' }), e('acked', '07:06')])
    assert.deepEqual(t.map((x) => [x.at, x.label, x.text]), [['07:05', '研判', '橙色'], ['07:00', '开事件', '开']])
  })

  it('只保留最近的若干条', () => {
    const entries = Array.from({ length: 40 }, (_, i) => ({ event_id: String(i), at: `t${i}`, wall: '', kind: 'note' as const, by: 'x', data: { type: 'assessment' } }))
    const t = timelineOf(entries)
    assert.equal(t.length, 14)
    assert.equal(t[0]!.at, 't39')
  })
})
