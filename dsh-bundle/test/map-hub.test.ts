import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { MapHub } from '../src/map/hub.ts'

function collect(hub: MapHub): { chunks: string[]; stop: () => void } {
  const chunks: string[] = []
  const stop = hub.subscribe((c) => chunks.push(c))
  return { chunks, stop }
}

describe('MapHub 指令校验', () => {
  it('focus、mark 必须带 name', () => {
    const hub = new MapHub()
    assert.throws(() => hub.emit({ op: 'focus' }), /需要 name/)
    assert.throws(() => hub.emit({ op: 'mark' }), /需要 name/)
  })

  it('拒绝未知操作、非法等级、越界的 zoom 和 tilt', () => {
    const hub = new MapHub()
    assert.throws(() => hub.emit({ op: 'explode' as never }), /未知操作/)
    assert.throws(() => hub.emit({ op: 'mark', name: 'a', level: 'purple' }), /level/)
    assert.throws(() => hub.emit({ op: 'focus', name: 'a', zoom: 99 }), /zoom/)
    assert.throws(() => hub.emit({ op: 'view', tilt: 90 }), /tilt/)
    assert.equal(hub.snapshot().seq, 0, '失败的指令不改变状态、不占序号')
    assert.doesNotThrow(() => hub.emit({ op: 'mark', name: 'a', level: 'orange' }), '预警等级也是合法的标记等级')
  })

  it('标记数量有上限，更新已有标记不占新名额', () => {
    const hub = new MapHub()
    for (let i = 0; i < 60; i++) hub.emit({ op: 'mark', id: `m${i}`, name: 'x' })
    assert.throws(() => hub.emit({ op: 'mark', id: 'extra', name: 'x' }), /最多/)
    assert.doesNotThrow(() => hub.emit({ op: 'mark', id: 'm0', name: 'y' }))
    assert.equal(hub.snapshot().marks.length, 60)
  })
})

describe('MapHub 状态与广播', () => {
  it('mark 同一编号是更新；unmark 删除；不存在的编号报错', () => {
    const hub = new MapHub()
    hub.emit({ op: 'mark', id: 'a', name: '211016采面', level: 'warn', text: '撤人中' })
    hub.emit({ op: 'mark', id: 'a', name: '211016采面', level: 'alarm', text: '撤人中' })
    assert.deepEqual(hub.snapshot().marks, [{ id: 'a', name: '211016采面', level: 'alarm', text: '撤人中' }])
    hub.emit({ op: 'unmark', id: 'a' })
    assert.equal(hub.snapshot().marks.length, 0)
    assert.throws(() => hub.emit({ op: 'unmark', id: 'a' }), /没有这个标记/)
  })

  it('没给 id 时自动编号，标记文字截到 60 字', () => {
    const hub = new MapHub()
    const e = hub.emit({ op: 'mark', name: 'n', text: '字'.repeat(100) })
    assert.match(e.cmd.id ?? '', /^m-\d+$/)
    assert.equal(hub.snapshot().marks[0]!.text.length, 60)
  })

  it('clear 清掉标记和定位，view 记下倾斜角', () => {
    const hub = new MapHub()
    hub.emit({ op: 'mark', id: 'a', name: 'n' })
    hub.emit({ op: 'focus', name: 'n', zoom: 3 })
    hub.emit({ op: 'view', tilt: 40 })
    assert.deepEqual(hub.snapshot().focus, { name: 'n', zoom: 3 })
    assert.equal(hub.snapshot().tilt, 40)
    hub.emit({ op: 'clear' })
    assert.deepEqual(hub.snapshot().marks, [])
    assert.equal(hub.snapshot().focus, null)
  })

  it('新订阅先收到快照，之后收到指令；取消订阅后不再收到', () => {
    const hub = new MapHub()
    hub.emit({ op: 'mark', id: 'a', name: 'n' })
    const { chunks, stop } = collect(hub)
    assert.match(chunks[0]!, /^event: snapshot\n/)
    assert.match(chunks[0]!, /"id":"a"/)
    hub.emit({ op: 'focus', name: 'n' })
    assert.match(chunks[1]!, /^event: cmd\n/)
    stop()
    hub.emit({ op: 'clear' })
    assert.equal(chunks.length, 2)
    assert.equal(hub.clientCount, 0)
  })

  it('写失败的连接被摘掉，不影响其他连接', () => {
    const hub = new MapHub()
    const good = collect(hub)
    let calls = 0
    hub.subscribe(() => {
      if (++calls > 1) throw new Error('broken pipe') // 第一次是快照，之后写失败
    })
    assert.equal(hub.clientCount, 2)
    hub.emit({ op: 'clear' })
    assert.equal(hub.clientCount, 1, '坏连接被移除')
    assert.equal(good.chunks.length, 2)
  })
})

describe('MapHub 图元（撤人路线等，只由代码生成）', () => {
  const line = (id: string) => ({ id, type: 'line' as const, kind: 'route', points: [[0, 0], [10, 10]] as [number, number][] })

  it('figure 新增或替换，unfigure 删除，快照里带着图元', () => {
    const hub = new MapHub()
    hub.emit({ op: 'figure', fig: line('auto:a') })
    hub.emit({ op: 'figure', fig: { ...line('auto:a'), text: '约 1.6 分钟' } })
    assert.equal(hub.snapshot().figures.length, 1)
    assert.equal(hub.snapshot().figures[0]!.text, '约 1.6 分钟')
    hub.emit({ op: 'unfigure', fig: { id: 'auto:a', type: 'line', kind: '' } })
    assert.equal(hub.snapshot().figures.length, 0)
    assert.throws(() => hub.emit({ op: 'unfigure', fig: { id: 'auto:a', type: 'line', kind: '' } }), /没有这个图元/)
  })

  it('校验：折线要 2 到 60 个有效的点，group 和 target 要 at，类型和数值要合法', () => {
    const hub = new MapHub()
    assert.throws(() => hub.emit({ op: 'figure' }), /图元需要 id/)
    assert.throws(() => hub.emit({ op: 'figure', fig: { ...line('x'), points: [[0, 0]] } }), /折线要/)
    assert.throws(() => hub.emit({ op: 'figure', fig: { ...line('x'), points: [[0, 0], [Number.NaN, 1]] } }), /折线要/)
    assert.throws(() => hub.emit({ op: 'figure', fig: { ...line('x'), points: Array.from({ length: 61 }, () => [0, 0] as [number, number]) } }), /折线要/)
    assert.throws(() => hub.emit({ op: 'figure', fig: { id: 'g', type: 'group', kind: 'threatened' } }), /需要 at/)
    assert.throws(() => hub.emit({ op: 'figure', fig: { id: 'g', type: 'blob' as never, kind: '' } }), /类型只能是/)
    assert.throws(() => hub.emit({ op: 'figure', fig: { ...line('x'), value: 'a' as never } }), /value 要是数字/)
    assert.throws(() => hub.emit({ op: 'figure', fig: { ...line('x'), at: [1] as never } }), /标签位置/)
    assert.equal(hub.snapshot().seq, 0, '被拒绝的图元不占序号')
  })

  it('文字截到 60 字；图元最多 120 个，更新已有的不占名额', () => {
    const hub = new MapHub()
    hub.emit({ op: 'figure', fig: { ...line('t'), text: '字'.repeat(100) } })
    assert.equal(hub.snapshot().figures[0]!.text!.length, 60)
    for (let i = 0; i < 119; i++) hub.emit({ op: 'figure', fig: line(`f${i}`) })
    assert.throws(() => hub.emit({ op: 'figure', fig: line('extra') }), /最多 120/)
    assert.doesNotThrow(() => hub.emit({ op: 'figure', fig: line('f0') }))
  })

  it('新订阅先收到带图元的快照；clear 不动图元', () => {
    const hub = new MapHub()
    hub.emit({ op: 'figure', fig: line('auto:a') })
    hub.emit({ op: 'clear' })
    const { chunks } = collect(hub)
    assert.match(chunks[0]!, /"figures":\[\{"id":"auto:a"/)
  })

  it('心跳是带当前序号的 ping 事件（浏览器据此发现漏收的指令）', () => {
    const hub = new MapHub()
    hub.emit({ op: 'focus', name: 'A' })
    hub.emit({ op: 'mark', id: 'x', name: 'A', level: 'red', text: 't' })
    assert.equal(hub.ping(), 'event: ping\ndata: {"seq":2}\n\n')
  })
})
