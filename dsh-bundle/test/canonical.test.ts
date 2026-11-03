import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, it } from 'node:test'

import { canonicalJson, contentSha256 } from '../src/evidence/canonical.ts'

// 与 Python 侧 mcp/contract/tests/test_canonical.py 共用样例
const cases: { name: string; value: unknown; canonical: string; sha256: string }[] = JSON.parse(
  readFileSync(new URL('../../mcp/contract/tests/fixtures/canonical.json', import.meta.url), 'utf8'),
)

describe('canonicalJson（与 Python 侧逐字节一致）', () => {
  for (const c of cases) {
    it(c.name, () => {
      if (typeof c.value !== 'string') assert.equal(canonicalJson(c.value), c.canonical)
      assert.equal(contentSha256(c.value), c.sha256)
    })
  }

  it('拒绝非有限数和超出安全范围的整数', () => {
    for (const v of [Number.NaN, Number.POSITIVE_INFINITY, 2 ** 53, 1e20]) assert.throws(() => canonicalJson(v))
  })

  it('值为 undefined 的键忽略（与 JSON.stringify 相同）', () => {
    assert.equal(canonicalJson({ b: 1, a: undefined }), '{"b":1}')
  })
})
