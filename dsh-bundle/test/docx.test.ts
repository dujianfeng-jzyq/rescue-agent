import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { markdownBlocks, markdownToDocx } from '../src/evidence/docx.ts'

describe('markdownToDocx', () => {
  it('输出 zip（docx）', async () => {
    const buf = await markdownToDocx('# 标题\n\n段落一\n接着\n\n- 要点 **重点**\n1. 步骤\n![风险图](a.svg)\n', '测试')
    assert.equal(buf.subarray(0, 2).toString(), 'PK')
    assert.ok(buf.length > 1000)
  })
  it('块结构：标题、段落合并、列表、图片行', () => {
    assert.equal(markdownBlocks('# T\n\nA\nB\n\n- x\n1. y\n![图](p.svg)').length, 5)
  })
})
