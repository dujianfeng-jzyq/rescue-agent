import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { Autopilot, computeOverlay, reconcile, reconcileFigures } from '../src/map/autopilot.ts'
import { anchorForSensor, parseMineMap } from '../src/map/mapping.ts'
import type { MineMap } from '../src/map/mapping.ts'
import type { MapState } from '../src/map/state.ts'

const NAMES = new Set(['211016采面', '211016回风巷', '211016运输巷'])
const MAP: MineMap = {
  panels: { '211016': { anchor: '211016采面', goafAnchor: '211016采面' } },
  roadways: { 'R-211016-RET': '211016回风巷', 'R-211016-IN': '211016运输巷', 'R-211016-FACE': '211016采面' },
  sensors: {},
}

function state(over: Partial<MapState> = {}): MapState {
  return {
    wall: '', mine: { now: null, ok: true }, sentinel: { known: true, ageS: 1, error: null }, incident: null, openIncidents: 0,
    readings: { panel: null, rows: [] }, alarms: { items: [] }, fireAreas: { items: [] }, records: [], agent: { state: 'idle', label: '待命' },
    ...over,
  }
}
const incident = (over: Record<string, unknown> = {}): NonNullable<MapState['incident']> => ({
  id: 'INC-1', panel: '211016', status: 'handling', statusName: '处置中', summary: '', openedAt: '', updatedAt: '', level: 'orange', levelName: '橙色',
  stage: 'accelerating', stageName: '加速氧化', alarmCount: 3, checkAsOf: null, latestAlertId: 'EVT-1', pendingAlerts: 0, degraded: false, followups: [],
  pendingApprovals: [], timeline: [], ...over,
}) as never
const alarm = (over: Record<string, unknown> = {}): MapState['alarms']['items'][number] => ({
  id: 'A1', sensorId: 'SM-211016-RET', sensor: '回风巷', measurand: 'CO', level: 'alarm', value: 47.3, threshold: 24, unit: 'ppm', message: '', raisedAt: '', anchor: '211016回风巷', ...over,
}) as never

describe('映射表', () => {
  it('名称不在地图上的条目丢弃并给出警告（宁可不显示，也不画错地方）', () => {
    const { map, warnings } = parseMineMap(`panels:\n  "211016": {anchor: 211016采面}\n  "999": {anchor: 不存在}\nroadways:\n  R1: 211016回风巷\n  R2: 乱写的\n`, NAMES)
    assert.deepEqual(Object.keys(map.panels), ['211016'])
    assert.deepEqual(Object.keys(map.roadways), ['R1'])
    assert.equal(warnings.length, 2)
    assert.match(warnings.join('\n'), /不存在/)
  })

  it('测点位置：测点覆盖 > 所在巷道 > 工作面（采空区测点用采空区锚点）', () => {
    const m: MineMap = { panels: { P: { anchor: 'A', goafAnchor: 'G' } }, roadways: { R: 'ROAD' }, sensors: { S0: 'OVERRIDE' } }
    assert.equal(anchorForSensor(m, { id: 'S0', roadway_id: 'R', panel: 'P' }), 'OVERRIDE')
    assert.equal(anchorForSensor(m, { id: 'S1', roadway_id: 'R', panel: 'P' }), 'ROAD')
    assert.equal(anchorForSensor(m, { id: 'S2', roadway_id: 'X', panel: 'P', role: 'goaf_gas_tube' }), 'G')
    assert.equal(anchorForSensor(m, { id: 'S3', roadway_id: 'X', panel: 'Q' }), null)
  })
})

describe('computeOverlay', () => {
  it('没有事件、报警、火区时什么都不画', () => {
    assert.deepEqual(computeOverlay(state(), MAP), [])
  })

  it('事件所在工作面按预警等级着色，标签是“等级 · 阶段”（来自检测摘要）', () => {
    const [m] = computeOverlay(state({ incident: incident() }), MAP)
    assert.deepEqual(m, { id: 'auto:211016采面', name: '211016采面', level: 'orange', text: '橙色 · 加速氧化' })
  })

  it('等级 none 画成正常（绿）、标签写“无预警”，没研判过画成提示（蓝）', () => {
    const none = computeOverlay(state({ incident: incident({ level: 'none', levelName: '无' }) }), MAP)[0]!
    assert.equal(none.level, 'ok')
    assert.match(none.text, /^无预警/)
    assert.equal(computeOverlay(state({ incident: incident({ level: null, levelName: null, stage: null, stageName: null }) }), MAP)[0]!.level, 'info')
  })

  it('矿井报警标在测点所在位置，标签写矿井原值；没有位置的报警不画', () => {
    const marks = computeOverlay(state({ alarms: { items: [alarm(), alarm({ id: 'A2', anchor: null })] } }), MAP)
    assert.equal(marks.length, 1)
    assert.equal(marks[0]!.name, '211016回风巷')
    assert.equal(marks[0]!.level, 'alarm')
    assert.match(marks[0]!.text, /CO 越限 47\.3ppm/)
  })

  it('同一个位置的多个来源合并成一条：有预警等级时颜色跟随预警等级，文字依次列出，多于两条报警写“等N项”', () => {
    const items = [alarm({ id: 'A1', anchor: '211016采面' }), alarm({ id: 'A2', anchor: '211016采面', measurand: 'CH4', value: 1.2, unit: '%' }), alarm({ id: 'A3', anchor: '211016采面', measurand: 'TEMP', value: 41, unit: '℃' })]
    const marks = computeOverlay(state({ incident: incident({ level: 'yellow', levelName: '黄色' }), alarms: { items }, fireAreas: { items: [{ id: 'F', panel: '211016', name: '火区', status: 'sealed', sealedAt: '', anchor: '211016采面' }] } }), MAP)
    assert.equal(marks.length, 1)
    assert.equal(marks[0]!.level, 'yellow', '颜色跟随预警等级，报警写在文字里')
    assert.match(marks[0]!.text, /黄色/)
    assert.match(marks[0]!.text, /等3项/)
    assert.ok(marks[0]!.text.length <= 60)
  })

  it('没有预警等级时，同一位置取各来源里最严重的颜色', () => {
    const items = [alarm({ id: 'A1', anchor: '211016采面', level: 'warning' })]
    const marks = computeOverlay(state({ alarms: { items }, fireAreas: { items: [{ id: 'F', panel: '211016', name: 'x', status: 'sealed', sealedAt: '', anchor: '211016采面' }] } }), MAP)
    assert.equal(marks[0]!.level, 'warn', '报警（warn）比火区提示（info）严重')
  })

  it('火区：已封闭、已启封各有说法', () => {
    const f = (status: string): MapState => state({ fireAreas: { items: [{ id: 'F', panel: '211016', name: 'x', status, sealedAt: '', anchor: '211016采面' }] } })
    assert.match(computeOverlay(f('sealed'), MAP)[0]!.text, /已封闭/)
    assert.match(computeOverlay(f('unsealed'), MAP)[0]!.text, /已启封/)
  })
})

describe('reconcile', () => {
  const want = [{ id: 'auto:A', name: 'A', level: 'warn', text: 't' }]
  it('新增、更新、删除；没变化不发指令；手动标记（非 auto:）不动', () => {
    const manual = { id: 'm-1', name: 'M', level: 'info', text: '手动' }
    assert.deepEqual(reconcile([manual], want).map((c) => c.op), ['mark'])
    assert.deepEqual(reconcile([manual, { id: 'auto:A', name: 'A', level: 'warn', text: 't' }], want), [])
    assert.deepEqual(reconcile([{ id: 'auto:A', name: 'A', level: 'red', text: 't' }], want).map((c) => c.op), ['mark'])
    assert.deepEqual(reconcile([manual, { id: 'auto:OLD', name: 'O', level: 'info', text: '' }], []), [{ op: 'unmark', id: 'auto:OLD' }])
  })

  it('系统标的位置上清掉智能体的标记（智能体按旧记录标的等级不会更新，两个圈会叠在一起）；别处的不动', () => {
    const stale = { id: 'agent:A', name: 'A', level: 'blue', text: '蓝色' }
    const other = { id: 'agent:B', name: 'B', level: 'info', text: '重点关注' }
    assert.deepEqual(reconcile([stale, other, { id: 'auto:A', name: 'A', level: 'warn', text: 't' }], want), [{ op: 'unmark', id: 'agent:A' }])
  })
})

describe('Autopilot', () => {
  it('第一次只记下已有告警，不飞；告警编号变了才飞到该事件所在的工作面', () => {
    const ap = new Autopilot()
    const s1 = state({ incident: incident({ latestAlertId: 'EVT-1' }) })
    assert.deepEqual(ap.step(s1, MAP, []).filter((c) => c.op === 'focus'), [])
    assert.deepEqual(ap.step(s1, MAP, []).filter((c) => c.op === 'focus'), [], '编号没变不飞')
    const s2 = state({ incident: incident({ latestAlertId: 'EVT-2' }) })
    assert.deepEqual(ap.step(s2, MAP, []).filter((c) => c.op === 'focus'), [{ op: 'focus', name: '211016采面', zoom: 3 }])
    assert.deepEqual(ap.step(s2, MAP, []).filter((c) => c.op === 'focus'), [], '只飞一次')
  })

  it('工作面没有映射时不飞，也不报错', () => {
    const ap = new Autopilot()
    ap.step(state({ incident: incident({ panel: '999', latestAlertId: 'E1' }) }), MAP, [])
    assert.deepEqual(ap.step(state({ incident: incident({ panel: '999', latestAlertId: 'E2' }) }), MAP, []).filter((c) => c.op === 'focus'), [])
  })

  it('事件转为已稳定时清掉智能体的标记，只在转入那一刻清（之后再标的保留）', () => {
    const ap = new Autopilot()
    const mark = { id: 'agent:211016回风巷', name: '211016回风巷', level: 'info', text: '重点关注' }
    const unmarks = (cmds: any[]) => cmds.filter((c) => c.op === 'unmark' && c.id.startsWith('agent:'))
    assert.deepEqual(unmarks(ap.step(state({ incident: incident({ status: 'handling' }) }), MAP, [mark])), [])
    assert.deepEqual(unmarks(ap.step(state({ incident: incident({ status: 'stable' }) }), MAP, [mark])), [{ op: 'unmark', id: mark.id }])
    assert.deepEqual(unmarks(ap.step(state({ incident: incident({ status: 'stable' }) }), MAP, [mark])), [], '稳定之后再标的不清')
    const fresh = new Autopilot() // 刚启动时事件已经是稳定的：不清（不知道标记是不是稳定之后标的）
    assert.deepEqual(unmarks(fresh.step(state({ incident: incident({ status: 'stable' }) }), MAP, [mark])), [])
  })

  it('事件关闭后标记被清掉', () => {
    const ap = new Autopilot()
    const marks = [{ id: 'auto:211016采面', name: '211016采面', level: 'orange', text: 'x' }]
    assert.deepEqual(ap.step(state(), MAP, marks), [{ op: 'unmark', id: 'auto:211016采面' }])
  })
})

describe('reconcileFigures', () => {
  const f = (id: string, text = 't') => ({ id, type: 'line' as const, kind: 'route', points: [[0, 0], [1, 1]] as [number, number][], text })

  it('新增、变化发 figure；不再需要的发 unfigure；没变化不发；非 auto: 的不动', () => {
    assert.deepEqual(reconcileFigures([], [f('auto:a')]).map((c) => c.op), ['figure'])
    assert.deepEqual(reconcileFigures([f('auto:a')], [f('auto:a')]), [])
    assert.deepEqual(reconcileFigures([f('auto:a', 'old')], [f('auto:a', 'new')]).map((c) => c.op), ['figure'])
    assert.deepEqual(reconcileFigures([f('auto:a'), f('manual')], []).map((c) => [c.op, c.fig!.id]), [['unfigure', 'auto:a']])
  })

  it('Autopilot：状态里的撤人图元变化时发指令；撤人记录没了（事件关闭、等级回落）就清掉', () => {
    const ap = new Autopilot()
    const st = (figures: ReturnType<typeof f>[]) => ({ ...state(), evacuation: { figures } as never })
    const first = ap.step(st([f('auto:evac:a')]), MAP, [], [])
    assert.deepEqual(first.filter((c) => c.op === 'figure').map((c) => c.fig!.id), ['auto:evac:a'])
    const cleared = ap.step(state(), MAP, [], [f('auto:evac:a')])
    assert.deepEqual(cleared.filter((c) => c.op === 'unfigure').map((c) => c.fig!.id), ['auto:evac:a'])
  })
})

