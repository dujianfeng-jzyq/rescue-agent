import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { unwrapToolValue } from '../src/fire-loop/tool-value.ts'

describe('unwrapToolValue：插件调 MCP 工具取 JSON 结果', () => {
  const found = { found: true, panel: '211016', issued_at: '2026-11-03T21:00:02' }
  it('rc.4 的 MCP 结果是 { content, structuredContent }：取 structuredContent', () => {
    assert.deepEqual(unwrapToolValue({ value: { content: [{ type: 'text', text: '{}' }], structuredContent: found } }), found)
  })
  it('没有 structuredContent 时解析文字块（顶层或 value 里的 content）', () => {
    const text = JSON.stringify(found)
    assert.deepEqual(unwrapToolValue({ content: [{ type: 'text', text }] }), found)
    assert.deepEqual(unwrapToolValue({ value: { content: [{ type: 'text', text }] } }), found)
  })
  it('插件工具直接返回的对象原样使用；什么都没有返回 null', () => {
    assert.deepEqual(unwrapToolValue({ value: found }), found)
    assert.equal(unwrapToolValue({}), null)
  })
})
