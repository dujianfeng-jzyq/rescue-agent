import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { toolValue } from '../src/fire-loop/tool-result.ts'

const TEXT = (v: unknown) => [{ type: 'text', text: JSON.stringify(v) }]

describe('固定流程取 MCP 工具结果', () => {
  it('value 就是结果时直接用', () => {
    assert.deepEqual(toolValue({ value: { found: true, issued_at: 'x' } }, 't', 'found'), { found: true, issued_at: 'x' })
  })
  it('value 是包装（没有关键字段）时解析文本块，found=false 也照实返回', () => {
    const wrapped = { value: { content: TEXT({ found: true }), structured: true }, content: TEXT({ found: true, plan_id: 'SP-1' }) }
    assert.deepEqual(toolValue(wrapped, 't', 'found'), { found: true, plan_id: 'SP-1' })
    assert.deepEqual(toolValue({ value: {}, content: TEXT({ found: false, panel: '1201' }) }, 't', 'found'), { found: false, panel: '1201' })
  })
  it('取不到时抛错，不当作“没有结果”', () => {
    assert.throws(() => toolValue({ value: { x: 1 } }, 'mcp__actions__query_last_issued', 'found'), /结果里没有 found/)
    assert.throws(() => toolValue({ content: [{ type: 'text', text: '不是 JSON' }] }, 't', 'found'), /结果里没有 found/)
    assert.throws(() => toolValue({ isError: true, error: { message: '连接失败' } }, 't', 'found'), /连接失败/)
  })
})
