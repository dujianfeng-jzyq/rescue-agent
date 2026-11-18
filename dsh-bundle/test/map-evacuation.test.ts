import assert from 'node:assert/strict'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import { ageHours, CONTAMINATION_LABEL_FRAC, evacuationView, fireSimCompareFor, freshnessOf, latestPersonnelRecord, pointAlong, routeCompareFor } from '../src/map/evacuation.ts'
import type { Network } from '../src/map/evacuation.ts'
import { EMPTY_MAP } from '../src/map/mapping.ts'
import type { MineMap } from '../src/map/mapping.ts'

const CASES = new URL('../../skills/fire-assessment/tests/cases/personnel/', import.meta.url)
const read = (name: string) => JSON.parse(readFileSync(new URL(name, CASES), 'utf8'))
const NETWORK: Network = read('s4-escalation-60h-red.input.json').evidence.network
const RESULT = read('s4-escalation-60h-red.expected.json').result

const record = (result: Record<string, unknown> = RESULT, id = 'RS-20261104T200000-aaaaaa') => ({
  record_id: id,
  kind: 'result',
  panel: '1201',
  as_of: String((result as { as_of?: string }).as_of ?? '2026-11-04T20:00:00'),
  source: { skill: 'fire-assessment', script: 'personnel' },
  content: { result },
})

// 10 个节点摆在图上（x 向右、y 向上），N8 避难硐室在 N2 旁边
const POS: Record<string, [number, number]> = {
  N0: [100, 700], N1: [200, 600], N2: [400, 300], N3: [700, 300], N4: [760, 400], N5: [500, 420], N6: [300, 440], N7: [100, 500], N8: [420, 260], N9: [250, 580],
}
const MAP: MineMap = {
  ...EMPTY_MAP,
  status: 'placeholder',
  nodes: POS,
  roadways: { 'R-1201-IN': '1201运输巷' },
  via: { 'R-1201-IN': [[550, 280]] },
}
const NOW_FRESH = '2026-11-04T21:30:00'

describe('evacuationView（s4 红色阶段的真实记录）', () => {
  const v = evacuationView(record(), NETWORK, MAP, NOW_FRESH)
  const fig = (id: string) => v.figures.find((f) => f.id === id)!

  it('合计、方法、分组的数字都是记录里的原值', () => {
    assert.equal(v.computed, true)
    assert.equal(v.totalThreatened, 11)
    assert.equal(v.totalUnderground, 22)
    assert.equal(v.method, RESULT.method)
    assert.deepEqual(v.groups.map((g) => [g.roadwayId, g.count, g.arrivalMin]), [['R-1201-FACE', 9, 0], ['R-1201-RET', 2, 1.6]])
    assert.equal(v.groups[0]!.route.lengthM, 1960)
    assert.equal(v.groups[0]!.route.walkMin, 32.7)
    assert.equal(v.groups[0]!.route.selfRescuerMin, 46.7)
    assert.equal(v.groups[0]!.route.targetName, '1201避难硐室')
  })

  it('污染巷道按记录画折线，值是到达时间原值，文字照抄', () => {
    const cont = v.figures.filter((f) => f.kind === 'contaminated')
    assert.deepEqual(cont.map((f) => f.id), ['auto:evac:cont:R-1201-FACE', 'auto:evac:cont:R-1201-RET', 'auto:evac:cont:R-RET-MAIN', 'auto:evac:cont:R-RET-SHAFT'])
    assert.equal(fig('auto:evac:cont:R-1201-RET').value, 1.6)
    assert.equal(fig('auto:evac:cont:R-1201-RET').text, '约 1.6 分钟到达')
    assert.deepEqual(fig('auto:evac:cont:R-1201-FACE').points, [POS.N3, POS.N4])
  })

  it('污染巷道的时间标签在入口一端（风流上游）往里一点，不在中点或末端', () => {
    // 到达时间是烟气到巷道入口的时间：R-1201-FACE 是 N3 → N4，标签在 N3 一端
    const f = fig('auto:evac:cont:R-1201-FACE')
    const [a, b] = [POS.N3, POS.N4]
    const k = CONTAMINATION_LABEL_FRAC
    assert.ok(k > 0 && k < 0.5)
    assert.deepEqual(f.at, [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k])
  })

  it('pointAlong 按长度取点（不是顶点下标）', () => {
    // 两点折线：0.5 是中点，不是终点
    assert.deepEqual(pointAlong([[0, 0], [10, 0]], 0.5), [5, 0])
    // 折线 0→(10,0)→(10,30)：总长 40，0.5 处在第二段的 10 处
    assert.deepEqual(pointAlong([[0, 0], [10, 0], [10, 30]], 0.5), [10, 10])
    assert.deepEqual(pointAlong([[0, 0], [10, 0], [10, 30]], 0), [0, 0])
    assert.deepEqual(pointAlong([[0, 0], [10, 0], [10, 30]], 1), [10, 30])
  })

  it('撤人路线：沿路线节点的顺序接起来（第一段巷道反向），起点是该组所在巷道的中点，终点是避难硐室', () => {
    const route = fig('auto:evac:route:R-1201-FACE')
    assert.equal(route.kind, 'route')
    assert.equal(route.text, '约 32.7 分钟')
    const pts = route.points!
    // 该组所在巷道 R-1201-FACE：N3(700,300) → N4(760,400)，中点按长度取
    assert.deepEqual(pts[0], [730, 350])
    // 路线节点 [N3, N2, N8]：R-1201-IN 本来是 N2→N3，这里要反向走：N3 → 途经点 → N2
    assert.deepEqual(pts.slice(1, 4), [POS.N3, [550, 280], POS.N2])
    assert.deepEqual(pts[pts.length - 1], POS.N8)
    const tgt = fig('auto:evac:target:R-1201-FACE')
    assert.equal(tgt.kind, 'refuge')
    assert.equal(tgt.text, '1201避难硐室')
    assert.deepEqual(tgt.at, POS.N8)
  })

  it('人数标记：值是记录里的人数；走不通的一组标“无可达路线，需人工研判”（记录原话），不画路线', () => {
    assert.equal(fig('auto:evac:group:R-1201-FACE').value, 9)
    assert.equal(fig('auto:evac:group:R-1201-FACE').text, '9 人')
    const blocked = fig('auto:evac:group:R-1201-RET')
    assert.equal(blocked.kind, 'blocked')
    assert.match(blocked.text!, /^2 人 · .*需人工研判/)
    assert.equal(v.figures.find((f) => f.id === 'auto:evac:route:R-1201-RET'), undefined)
    assert.equal(v.groups[1]!.route.reachable, false)
    assert.match(v.groups[1]!.route.note ?? '', /需人工研判/)
  })

  it('示意映射（没有核对人）时 schematic 为真', () => {
    assert.equal(v.schematic, true)
    assert.equal(evacuationView(record(), NETWORK, { ...MAP, status: 'reviewed', reviewedBy: '张三' }, NOW_FRESH).schematic, false)
  })
})

describe('缺映射：宁可不画，也不画错', () => {
  it('路线里有一个节点没有位置：整条路线不画（不画一半），写出缺的节点；其余图元照画', () => {
    const { N8: _drop, ...nodes } = POS
    const v = evacuationView(record(), NETWORK, { ...MAP, nodes }, NOW_FRESH)
    assert.equal(v.figures.find((f) => f.id === 'auto:evac:route:R-1201-FACE'), undefined)
    assert.equal(v.groups[0]!.route.drawn, false)
    assert.deepEqual(v.groups[0]!.route.missing, ['N8'])
    assert.ok(v.figures.find((f) => f.id === 'auto:evac:group:R-1201-FACE'), '人数标记仍然画')
    assert.equal(v.figures.filter((f) => f.kind === 'contaminated').length, 4)
  })

  it('一条污染巷道的节点缺位置：只那一条不画，其余照画；列表里标 drawn=false', () => {
    const { N6: _drop, ...nodes } = POS
    const v = evacuationView(record(), NETWORK, { ...MAP, nodes }, NOW_FRESH)
    const bad = v.contaminated.filter((c) => !c.drawn).map((c) => c.roadwayId)
    assert.ok(bad.includes('R-RET-MAIN') || bad.includes('R-RET-SHAFT'))
    assert.ok(v.contaminated.some((c) => c.drawn))
  })

  it('完全没有映射：什么图元都没有，但分组、路线文字照常给出（左栏用）', () => {
    const v = evacuationView(record(), NETWORK, EMPTY_MAP, NOW_FRESH)
    assert.deepEqual(v.figures, [])
    assert.equal(v.groups.length, 2)
    assert.equal(v.groups[0]!.route.walkMin, 32.7)
  })

  it('没有网络信息（取不到）：不画，不抛错', () => {
    const v = evacuationView(record(), null, MAP, NOW_FRESH)
    assert.deepEqual(v.figures, [])
  })
})

describe('时效', () => {
  const at = (now: string) => evacuationView(record(), NETWORK, MAP, now)

  it('2 小时内新鲜；2 到 6 小时变灰（图元 stale）；超过 6 小时不再显示图元但列表保留', () => {
    assert.equal(at('2026-11-04T21:59:00').freshness, 'fresh')
    assert.ok(at('2026-11-04T21:59:00').figures.every((f) => !f.stale))
    const grey = at('2026-11-05T00:00:00')
    assert.equal(grey.freshness, 'grey')
    assert.ok(grey.figures.length > 0 && grey.figures.every((f) => f.stale))
    const hidden = at('2026-11-05T03:00:00')
    assert.equal(hidden.freshness, 'hidden')
    assert.deepEqual(hidden.figures, [])
    assert.equal(hidden.groups.length, 2)
  })

  it('不知道矿井时间：按“无法判断”处理（图元变灰），不当成新鲜', () => {
    const v = evacuationView(record(), NETWORK, MAP, null)
    assert.equal(v.freshness, 'unknown')
    assert.ok(v.figures.every((f) => f.stale))
  })

  it('ageHours、freshnessOf', () => {
    assert.equal(ageHours('2026-11-04T20:00:00', '2026-11-04T21:30:00'), 1.5)
    assert.equal(ageHours('2026-11-04T20:00:00', null), null)
    assert.equal(freshnessOf(2), 'fresh')
    assert.equal(freshnessOf(2.01), 'grey')
    assert.equal(freshnessOf(6.01), 'hidden')
  })
})

describe('没有计算', () => {
  it('computed=false：给出记录里的原因，没有图元', () => {
    const r = read('s1-normal-not-computed.expected.json').result
    const v = evacuationView(record(r, 'RS-20261103T080000-bbbbbb'), NETWORK, MAP, '2026-11-03T08:10:00')
    assert.equal(v.computed, false)
    assert.match(v.note ?? '', /未计算受威胁人员/)
    assert.deepEqual(v.figures, [])
  })
})

describe('latestPersonnelRecord', () => {
  async function dir() {
    return mkdtemp(join(tmpdir(), 'evac-'))
  }
  const put = (d: string, r: ReturnType<typeof record>) => writeFile(join(d, `${r.record_id}.json`), JSON.stringify(r))

  it('取该工作面 as_of 最新的 personnel 记录；最新的是“未计算”时就是它，不退回更早的“已计算”', async () => {
    const d = await dir()
    await put(d, record(RESULT, 'RS-20261104T200000-aaaaaa'))
    const later = { ...read('s1-normal-not-computed.expected.json').result, as_of: '2026-11-04T22:00:00' }
    await put(d, { ...record(later, 'RS-20261104T220000-bbbbbb'), as_of: '2026-11-04T22:00:00' })
    const r = await latestPersonnelRecord(d, '1201')
    assert.equal(r?.record_id, 'RS-20261104T220000-bbbbbb')
  })

  it('给了当前矿井时间：as_of 晚于它的记录（时钟重置前留下的）不选，选不晚于它的最新一份', async () => {
    const d = await dir()
    await put(d, record(RESULT, 'RS-20261104T200000-aaaaaa'))
    await put(d, { ...record(RESULT, 'RS-20261104T230000-bbbbbb'), as_of: '2026-11-04T23:00:00' })
    assert.equal((await latestPersonnelRecord(d, '1201', '2026-11-04T21:00:00'))?.record_id, 'RS-20261104T200000-aaaaaa')
    assert.equal((await latestPersonnelRecord(d, '1201', '2026-11-04T19:00:00')), null)
    assert.equal((await latestPersonnelRecord(d, '1201', null))?.record_id, 'RS-20261104T230000-bbbbbb')
  })

  it('不同工作面、别的技能的记录、坏文件都不选；没有时为 null', async () => {
    const d = await dir()
    await put(d, { ...record(RESULT, 'RS-20261104T200000-cccccc'), panel: '1202', content: { result: { ...RESULT, panel: '1202' } } } as never)
    await put(d, { ...record(RESULT, 'RS-20261104T200100-dddddd'), source: { skill: 'fire-assessment', script: 'warning' } } as never)
    await writeFile(join(d, 'RS-20261104T200200-eeeeee.json'), '{坏的')
    assert.equal(await latestPersonnelRecord(d, '1201'), null)
    assert.equal(await latestPersonnelRecord(join(d, 'nope'), '1201'), null)
  })
})

// 第六版：外部服务的参考路线（第二意见）。比较结果用 route_compare 的自检样例（烟中时间上限放宽：回风巷那组得到穿烟路线）
const CMP_CASES = new URL('../../skills/fire-assessment/tests/cases/route_compare/', import.meta.url)
const CMP = JSON.parse(readFileSync(new URL('s4-through-smoke-raised-limit.expected.json', CMP_CASES), 'utf8'))
const CMP_NONE = JSON.parse(readFileSync(new URL('s4-escalation-60h-red-service.expected.json', CMP_CASES), 'utf8'))
const compareRecord = (personnelId: string, content = CMP, id = 'RS-20261104T200000-cmp001') => ({
  record_id: id,
  kind: 'result',
  panel: '1201',
  as_of: '2026-11-04T20:00:00',
  source: { skill: 'fire-assessment', script: 'route_compare', inputs: { personnel: personnelId } },
  content,
})

describe('参考路线（外部服务，第二意见）', () => {
  const MAP_ALL: MineMap = { ...MAP, roadways: {} }
  const P = 'RS-20261104T200000-aaaaaa'

  it('同一次研判的比较记录：每组附比较说明；有路线的另画一条 reference 折线，与我方路线并列', () => {
    const v = evacuationView(record(RESULT, P), NETWORK, MAP_ALL, NOW_FRESH, compareRecord(P))
    assert.equal(v.referenceRecordId, 'RS-20261104T200000-cmp001')
    const [face, ret] = v.groups
    assert.equal(face!.reference?.caseText, '两种路线相同')
    assert.equal(ret!.reference?.status, 'route')
    assert.match(ret!.reference!.caseText, /能否穿烟由人判断/)
    assert.equal(ret!.reference!.text, CMP.result.groups[1].text)
    const ref = v.figures.find((f) => f.id === 'auto:evac:ref:R-1201-RET')!
    assert.equal(ref.kind, 'reference')
    assert.equal(ref.text, '参考路线（外部服务，试验性）')
    // 从回风巷中点出发，经工作面（反向）N4 → N3、运输巷（反向，含途经点）N3 → N2、联络巷 N2 → 避难硐室 N8（与我方路线一样，各段首尾相接处重复一个点）
    assert.deepEqual(ref.points, [[630, 410], POS.N4, POS.N3, POS.N3, [550, 280], POS.N2, POS.N2, POS.N8])
    // 我方这一组走不通：没有我方路线，只有参考路线
    assert.equal(v.figures.find((f) => f.id === 'auto:evac:route:R-1201-RET'), undefined)
    assert.ok(v.figures.find((f) => f.id === 'auto:evac:route:R-1201-FACE'))
    assert.ok(v.figures.find((f) => f.id === 'auto:evac:ref:R-1201-FACE'))
  })

  it('比较记录不是这份 personnel 记录的（别的研判）：不用', () => {
    const v = evacuationView(record(RESULT, P), NETWORK, MAP_ALL, NOW_FRESH, compareRecord('RS-20261104T190000-zzzzzz'))
    assert.equal(v.referenceRecordId, null)
    assert.ok(v.groups.every((g) => g.reference === null))
    assert.equal(v.figures.filter((f) => f.kind === 'reference').length, 0)
  })

  it('服务没有给出路线：只有文字，不画线', () => {
    const v = evacuationView(record(RESULT, P), NETWORK, MAP_ALL, NOW_FRESH, compareRecord(P, CMP_NONE))
    assert.equal(v.groups[1]!.reference?.status, 'no_route')
    assert.equal(v.groups[1]!.reference?.caseText, '两种方法都没有给出路线，需人工研判')
    assert.equal(v.figures.find((f) => f.id === 'auto:evac:ref:R-1201-RET'), undefined)
  })

  it('routeCompareFor 按输入的 personnel 编号找比较记录', async () => {
    const d = await mkdtemp(join(tmpdir(), 'evac-ref-'))
    await writeFile(join(d, `${P}.json`), JSON.stringify(record(RESULT, P)))
    await writeFile(join(d, 'RS-20261104T200000-cmp001.json'), JSON.stringify(compareRecord(P)))
    await writeFile(join(d, 'RS-20261104T190000-cmp002.json'), JSON.stringify(compareRecord('RS-20261104T190000-other0', CMP, 'RS-20261104T190000-cmp002')))
    assert.equal((await routeCompareFor(d, P, '1201'))?.record_id, 'RS-20261104T200000-cmp001')
    assert.equal(await routeCompareFor(d, 'RS-20261104T180000-nonexx', '1201'), null)
    assert.equal((await latestPersonnelRecord(d, '1201'))?.record_id, P)
  })
})

// 第七版：火灾蔓延第二意见。判读比较结果用 fire_sim_compare 的自检样例（实测返回、人为改写的倒灌、提供方 500）
const FIRE_CASES = new URL('../../skills/fire-assessment/tests/cases/fire_sim_compare/', import.meta.url)
const fireCase = (name: string) => JSON.parse(readFileSync(new URL(`${name}.expected.json`, FIRE_CASES), 'utf8'))
const fireRecord = (personnelId: string, content = fireCase('s4-escalation-60h-red-service'), id = 'RS-20261104T200000-fire01') => ({
  record_id: id,
  kind: 'result',
  panel: '1201',
  as_of: '2026-11-04T20:00:00',
  source: { skill: 'fire-assessment', script: 'fire_sim_compare', inputs: { personnel: personnelId } },
  content,
})

describe('火灾蔓延第二意见（外部火灾推演服务）', () => {
  const MAP_ALL: MineMap = { ...MAP, roadways: {} }
  const P = 'RS-20261104T200000-aaaaaa'

  it('同一次研判的记录：推演中 CO 到达的巷道各画一条 fire_spread 线，带到达和危险时刻（记录原值）', () => {
    const v = evacuationView(record(RESULT, P), NETWORK, MAP_ALL, NOW_FRESH, null, fireRecord(P))
    const fs = v.fireSpread!
    assert.equal(fs.recordId, 'RS-20261104T200000-fire01')
    assert.equal(fs.status, 'ok')
    assert.equal(fs.horizonMin, 60)
    assert.deepEqual([fs.reachPpm, fs.dangerPpm], [24, 400])
    assert.deepEqual(fs.lines, fireCase('s4-escalation-60h-red-service').result.lines)
    assert.deepEqual(fs.roadways.map((r) => [r.roadwayId, r.reachMin, r.dangerMin, r.peakPpm]), [
      ['R-1201-GOAF', 1, 3, 3700],
      ['R-1201-RET', 3, null, 340],
      ['R-RET-MAIN', 9, null, 340],
      ['R-RET-SHAFT', 16, null, 130],
    ])
    const lines = v.figures.filter((f) => f.kind === 'fire_spread')
    assert.deepEqual(lines.map((f) => [f.id, f.value, f.danger, f.text]), [
      ['auto:fire:R-1201-GOAF', 1, 3, '推演 1 分钟'],
      ['auto:fire:R-1201-RET', 3, null, '推演 3 分钟'],
      ['auto:fire:R-RET-MAIN', 9, null, '推演 9 分钟'],
      ['auto:fire:R-RET-SHAFT', 16, null, '推演 16 分钟'],
    ])
    assert.deepEqual(lines[1]!.points, [POS.N4, POS.N5])
    // 我方污染巷道照常画，两层并列
    assert.equal(v.figures.filter((f) => f.kind === 'contaminated').length, 4)
  })

  it('风流反转写进 flowChanged；没有映射的巷道列出但不画', () => {
    const map: MineMap = { ...MAP_ALL, nodes: Object.fromEntries(Object.entries(POS).filter(([k]) => k !== 'N7')) }
    const v = evacuationView(record(RESULT, P), NETWORK, map, NOW_FRESH, null, fireRecord(P, fireCase('s4-synthetic-backflow')))
    const fs = v.fireSpread!
    assert.deepEqual(fs.flowChanged, ['R-1201-IN'])
    const shaft = fs.roadways.find((r) => r.roadwayId === 'R-RET-SHAFT')!
    assert.equal(shaft.drawn, false)
    assert.equal(v.figures.find((f) => f.id === 'auto:fire:R-RET-SHAFT'), undefined)
    assert.equal(fs.roadways.find((r) => r.roadwayId === 'R-1201-IN')!.reversed, true)
  })

  it('第二意见不可用：只有原因，不画线；记录不是这份 personnel 的：不用', () => {
    const down = evacuationView(record(RESULT, P), NETWORK, MAP_ALL, NOW_FRESH, null, fireRecord(P, fireCase('s4-provider-500')))
    assert.equal(down.fireSpread?.status, 'unavailable')
    assert.match(down.fireSpread!.note!, /服务返回 500/)
    assert.equal(down.figures.filter((f) => f.kind === 'fire_spread').length, 0)
    const other = evacuationView(record(RESULT, P), NETWORK, MAP_ALL, NOW_FRESH, null, fireRecord('RS-20261104T190000-zzzzzz'))
    assert.equal(other.fireSpread, null)
  })

  it('记录过期太久：文字保留，图上不画', () => {
    const v = evacuationView(record(RESULT, P), NETWORK, MAP_ALL, '2026-11-05T09:00:00', null, fireRecord(P))
    assert.equal(v.fireSpread?.roadways.length, 4)
    assert.equal(v.figures.filter((f) => f.kind === 'fire_spread').length, 0)
  })

  it('fireSimCompareFor 按输入的 personnel 编号找记录', async () => {
    const d = await mkdtemp(join(tmpdir(), 'evac-fire-'))
    await writeFile(join(d, `${P}.json`), JSON.stringify(record(RESULT, P)))
    await writeFile(join(d, 'RS-20261104T200000-fire01.json'), JSON.stringify(fireRecord(P)))
    await writeFile(join(d, 'RS-20261104T190000-fire02.json'), JSON.stringify(fireRecord('RS-20261104T190000-other0', undefined, 'RS-20261104T190000-fire02')))
    assert.equal((await fireSimCompareFor(d, P, '1201'))?.record_id, 'RS-20261104T200000-fire01')
    assert.equal(await fireSimCompareFor(d, 'RS-nope', '1201'), null)
  })
})
