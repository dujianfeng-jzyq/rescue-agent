import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { EMPTY_MAP, isSchematic, parseMineMap } from '../src/map/mapping.ts'

const NAMES = new Set(['211016采面', '211016回风巷'])
const BBOX: [number, number, number, number] = [0, 0, 1000, 800]

describe('映射表：节点位置和途经点', () => {
  it('节点位置、途经点、核对状态', () => {
    const { map, warnings } = parseMineMap(
      `status: placeholder\nreviewed_by: 张三\nnodes:\n  N0: {at: [10, 20]}\n  N1: {at: [500.5, 400]}\nroadways:\n  R1: {name: 211016采面, via: [[100, 100], [200, 150]]}\n  R2: 211016回风巷\n  R3: {}\n`,
      NAMES,
      BBOX,
    )
    assert.deepEqual(warnings, [])
    assert.deepEqual(map.nodes, { N0: [10, 20], N1: [500.5, 400] })
    assert.deepEqual(map.via.R1, [[100, 100], [200, 150]])
    assert.equal(map.roadways.R1, '211016采面')
    assert.equal(map.roadways.R2, '211016回风巷')
    assert.equal(map.status, 'placeholder')
    assert.equal(map.reviewedBy, '张三')
  })

  it('位置格式不对、出界的节点丢弃并警告（不画到图外）', () => {
    const { map, warnings } = parseMineMap(`nodes:\n  A: {at: [10]}\n  B: {at: [10, "x"]}\n  C: {at: [5000, 5]}\n  D: {at: [900, 700]}\n`, NAMES, BBOX)
    assert.deepEqual(Object.keys(map.nodes), ['D'])
    assert.equal(warnings.length, 3)
    assert.match(warnings.join('\n'), /图的范围之外/)
  })

  it('途经点里有一个无效，整条途经点丢弃（按直线画），不画一半', () => {
    const { map, warnings } = parseMineMap(`roadways:\n  R1: {via: [[100, 100], [9999, 9999]]}\n`, NAMES, BBOX)
    assert.equal(map.via.R1, undefined)
    assert.match(warnings.join('\n'), /按直线画/)
  })

  it('名称不在地图上仍然丢弃（原有规则不变）；字符串写法仍然可用', () => {
    const { map, warnings } = parseMineMap(`roadways:\n  R1: 乱写的\n  R2: {name: 也乱写}\n`, NAMES, BBOX)
    assert.deepEqual(map.roadways, {})
    assert.equal(warnings.length, 2)
  })

  it('没有范围信息时不校验位置（地图包里没有 geometry.json）', () => {
    const { map } = parseMineMap(`nodes:\n  A: {at: [99999, -5]}\n`, null, null)
    assert.deepEqual(map.nodes.A, [99999, -5])
  })
})

describe('示意映射', () => {
  it('没有核对人、或状态还是 placeholder，都是“示意映射”', () => {
    assert.equal(isSchematic(EMPTY_MAP), true)
    assert.equal(isSchematic({ ...EMPTY_MAP, status: 'placeholder', reviewedBy: '张三' }), true)
    assert.equal(isSchematic({ ...EMPTY_MAP, status: 'reviewed', reviewedBy: null }), true)
    assert.equal(isSchematic({ ...EMPTY_MAP, status: 'reviewed', reviewedBy: '张三' }), false)
  })
})
