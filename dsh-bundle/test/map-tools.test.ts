import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { MapHub } from '../src/map/hub.ts'
import { labelFromRecord } from '../src/map/record-label.ts'
import type { RecordLike } from '../src/map/record-label.ts'
import { MAX_AGENT_MARKS, NOTES, nearest, runMapView } from '../src/map/tools.ts'
import type { MapViewArgs } from '../src/map/tools.ts'

const NAMES = new Set(['211016采面', '211016回风巷', '211016运输巷', '216回风北石门', '1200回风平巷'])
const RS_WARNING = 'RS-20261102T143541-aaaaaa'
const RS_STAGE = 'RS-20261102T143541-bbbbbb'
const RS_ALARMS = 'RS-20261102T143541-cccccc'
const RP_UNKNOWN = 'RP-20261102T143541-dddddd'
const RECORDS: Record<string, RecordLike> = {
  [RS_WARNING]: { record_id: RS_WARNING, kind: 'result', source: { skill: 'fire-assessment', script: 'warning' }, content: { result: { level: 'orange', level_name: '橙色（严重）' } } },
  [RS_STAGE]: { record_id: RS_STAGE, kind: 'result', source: { skill: 'fire-assessment', script: 'stage' }, content: { result: { stage: 'accelerating', stage_name: '加速氧化' } } },
  [RS_ALARMS]: { record_id: RS_ALARMS, kind: 'result', source: { skill: 'fire-assessment', script: 'alarms' }, content: { result: { count: 3 } } },
  [RP_UNKNOWN]: { record_id: RP_UNKNOWN, kind: 'report', source: { skill: 'incident-review', script: 'timeline' }, content: { result: {} } },
}

function setup() {
  const hub = new MapHub()
  const call = (args: MapViewArgs) => runMapView(args, { hub, names: NAMES, readRecord: async (id) => RECORDS[id] ?? null })
  return { hub, call }
}

describe('map_view：名称', () => {
  it('地图上没有的名称被拒绝，并给出相近的名称', async () => {
    const { call, hub } = setup()
    await assert.rejects(call({ action: 'focus', name: '211016工作面' }), /没有叫“211016工作面”.*相近的有：.*211016/)
    await assert.rejects(call({ action: 'mark', name: '乱写的', note: '重点关注' }), /没有叫/)
    assert.equal(hub.snapshot().seq, 0, '被拒绝的动作不发任何指令')
  })

  it('list：不带 query 列全部（排序），带 query 按相近程度', async () => {
    const { call } = setup()
    const all = (await call({ action: 'list' })) as { names: string[]; count: number }
    assert.equal(all.count, 5)
    assert.deepEqual(all.names, [...NAMES].sort())
    const q = (await call({ action: 'list', query: '回风' })) as { names: string[] }
    assert.ok(q.names.includes('211016回风巷') && q.names.includes('216回风北石门'))
    assert.ok(!q.names.includes('211016采面'))
  })

  it('list 查不到时明确告诉智能体地图上没有，不要换说法反复查；查到了不带这句提示', async () => {
    const { call } = setup()
    const none = (await call({ action: 'list', query: '皮带' })) as { shown: number; note?: string }
    assert.equal(none.shown, 0)
    assert.match(none.note ?? '', /地图上没有.*不要换说法反复查/)
    const some = (await call({ action: 'list', query: '回风巷' })) as { note?: string }
    assert.equal(some.note, undefined, '查到了就不该有“只列了一部分”之类的提示')
  })

  it('nearest：互相包含的优先', () => {
    assert.equal(nearest(NAMES, '211016采面')[0], '211016采面')
    assert.equal(nearest(NAMES, '回风巷')[0], '211016回风巷')
    assert.deepEqual(nearest(NAMES, ''), [])
  })
})

describe('map_view：focus、view、reset', () => {
  it('focus 缺省放大 4 倍，指令发给指令中心', async () => {
    const { call, hub } = setup()
    await call({ action: 'focus', name: '211016采面' })
    assert.deepEqual(hub.snapshot().focus, { name: '211016采面', zoom: 4 })
  })

  it('zoom、tilt 越界由指令中心拒绝', async () => {
    const { call } = setup()
    await assert.rejects(call({ action: 'focus', name: '211016采面', zoom: 99 }), /zoom/)
    await assert.rejects(call({ action: 'view', tilt: 90 }), /tilt/)
    await assert.rejects(call({ action: 'view' }), /需要 tilt/)
  })

  it('reset 清掉定位并把倾斜归零', async () => {
    const { call, hub } = setup()
    await call({ action: 'focus', name: '211016采面' })
    await call({ action: 'view', tilt: 40 })
    await call({ action: 'reset' })
    assert.equal(hub.snapshot().focus, null)
    assert.equal(hub.snapshot().tilt, 0)
  })
})

describe('map_view：mark 的依据', () => {
  it('带预警等级记录：标签和颜色由代码从记录取', async () => {
    const { call, hub } = setup()
    const r = (await call({ action: 'mark', name: '211016采面', record_id: RS_WARNING, note: '重点关注' })) as { text: string; level: string }
    assert.equal(r.text, '重点关注 · 橙色（严重）')
    assert.equal(r.level, 'orange')
    assert.deepEqual(hub.snapshot().marks[0], { id: 'agent:211016采面', name: '211016采面', level: 'orange', text: '重点关注 · 橙色（严重）' })
  })

  it('阶段、报警项数记录取原值；报告类等不认识的记录退化成“依据：记录编号”', async () => {
    const { call } = setup()
    assert.equal(((await call({ action: 'mark', name: '211016采面', record_id: RS_STAGE })) as any).text, '加速氧化')
    assert.equal(((await call({ action: 'mark', name: '211016回风巷', record_id: RS_ALARMS })) as any).text, '报警 3 项')
    assert.match(((await call({ action: 'mark', name: '216回风北石门', record_id: RP_UNKNOWN })) as any).text, /^依据 报告 RP-/)
  })

  it('备注是封闭词表：自由文字一律拒绝（对位置的断言和数值都不能由模型写）', async () => {
    const { call, hub } = setup()
    for (const bad of ['主要避灾撤人通道', '建议撤人方向', 'CO 47', '４７', '三号面', '随便写点什么']) {
      await assert.rejects(call({ action: 'mark', name: '211016采面', note: bad }), /note 只能从这几个里选/)
    }
    assert.equal(hub.snapshot().marks.length, 0)
    for (const ok of NOTES) await call({ action: 'mark', name: '211016采面', note: ok })
    assert.equal(hub.snapshot().marks[0]!.level, 'info', '没有记录依据只能是提示色')
  })

  it('既没有记录也没有备注、备注太长、记录不存在或格式不对都拒绝', async () => {
    const { call } = setup()
    await assert.rejects(call({ action: 'mark', name: '211016采面' }), /需要 note/)
    await assert.rejects(call({ action: 'mark', name: '211016采面', record_id: 'RS-20261102T143541-zzzzzz' }), /找不到记录/)
    await assert.rejects(call({ action: 'mark', name: '211016采面', record_id: '../../etc/passwd' }), /格式不对/)
  })

  it('同一个位置再标是更新；智能体的标记最多 12 个', async () => {
    const { call, hub } = setup()
    await call({ action: 'mark', name: '211016采面', note: '正在查看' })
    await call({ action: 'mark', name: '211016采面', note: '重点关注' })
    assert.equal(hub.snapshot().marks.length, 1)
    for (let i = 0; i < MAX_AGENT_MARKS; i++) hub.emit({ op: 'mark', id: `agent:x${i}`, name: '211016运输巷', text: 't' })
    await assert.rejects(call({ action: 'mark', name: '216回风北石门', note: '问题所在' }), /已有 12 个/)
    await assert.doesNotReject(call({ action: 'mark', name: '211016采面', note: '需要核查' }))
  })
})

describe('map_view：unmark、clear 只动智能体自己的标记', () => {
  it('不动系统自动标的（auto:）和别处标的', async () => {
    const { call, hub } = setup()
    hub.emit({ op: 'mark', id: 'auto:211016采面', name: '211016采面', level: 'orange', text: '橙色' })
    hub.emit({ op: 'mark', id: 'm-9', name: '211016运输巷', text: '手动' })
    await call({ action: 'mark', name: '211016回风巷', note: '重点关注' })
    await call({ action: 'clear' })
    assert.deepEqual(hub.snapshot().marks.map((m) => m.id).sort(), ['auto:211016采面', 'm-9'])
    await assert.rejects(call({ action: 'unmark', name: '211016采面' }), /没有在“211016采面”上标记/, '系统标的不是智能体的，取消不了')
  })

  it('unmark 取消自己的标记', async () => {
    const { call, hub } = setup()
    await call({ action: 'mark', name: '211016回风巷', note: '重点关注' })
    await call({ action: 'unmark', name: '211016回风巷' })
    assert.equal(hub.snapshot().marks.length, 0)
  })
})

describe('labelFromRecord', () => {
  it('预警等级记录给出等级，其余为 null', () => {
    assert.deepEqual(labelFromRecord(RECORDS[RS_WARNING]!), { text: '橙色（严重）', warningLevel: 'orange' })
    assert.equal(labelFromRecord(RECORDS[RS_STAGE]!).warningLevel, null)
  })

  it('字段缺失时退化，不抛错', () => {
    const r: RecordLike = { record_id: 'RS-20261102T143541-eeeeee', kind: 'result', source: { skill: 'fire-assessment', script: 'warning' }, content: { result: {} } }
    assert.match(labelFromRecord(r).text, /^依据 /)
  })
})
