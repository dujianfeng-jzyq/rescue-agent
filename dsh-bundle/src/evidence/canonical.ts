/**
 * 记录内容的规范化 JSON（RFC 8785，JCS）和 content_sha256，与 Python 侧 rescue_contract.canonical 逐字节一致。
 *
 * 键按 UTF-16 码元排序（JS 字符串默认比较就是这样），数字和字符串用 JSON.stringify 的输出（即 ECMAScript 规则）。
 * 两边用 mcp/contract/tests/fixtures/canonical.json 的同一组样例测试。
 */

import { createHash } from 'node:crypto'

export function canonicalJson(value: unknown): string {
  if (value === null) return 'null'
  switch (typeof value) {
    case 'boolean':
      return value ? 'true' : 'false'
    case 'number':
      if (!Number.isFinite(value)) throw new Error(`非有限数 ${value} 不能写进 JSON`)
      if (Number.isInteger(value) && !Number.isSafeInteger(value) && Math.abs(value) < 1e21) {
        throw new Error(`整数 ${value} 超出 JS 安全整数范围，不能规范化`)
      }
      return JSON.stringify(value)
    case 'string':
      return JSON.stringify(value)
    case 'object': {
      if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
      const obj = value as Record<string, unknown>
      const keys = Object.keys(obj).filter((k) => obj[k] !== undefined).sort()
      return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`).join(',')}}`
    }
    default:
      throw new Error(`不能规范化的类型：${typeof value}`)
  }
}

/** 字符串取其 UTF-8 字节，其余取规范化 JSON 的 UTF-8 字节。 */
export function contentSha256(content: unknown): string {
  const data = typeof content === 'string' ? content : canonicalJson(content)
  return createHash('sha256').update(data, 'utf8').digest('hex')
}
