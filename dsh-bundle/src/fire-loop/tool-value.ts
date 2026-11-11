/**
 * 插件经 ctx.tools.execute 调 MCP 工具时取出 JSON 结果。
 * rc.4 上 MCP 工具的 value 是 `{ content, structuredContent }`（验证清单 #19），不是工具返回的对象本身：
 * 原来直接返回 value，review_effect 的 query_last_issued 结果 `found` 总是取不到，一律当成“台账里没有已下发的方案”。
 */
export function unwrapToolValue(r: { value?: unknown; content?: unknown }): any {
  const v = r.value as any
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    const sc = v.structuredContent
    if (sc && typeof sc === 'object' && !Array.isArray(sc)) return sc
    if (!('content' in v)) return v
  }
  const blocks = (Array.isArray(r.content) ? r.content : Array.isArray(v?.content) ? v.content : []) as any[]
  const text = blocks.find((b) => b?.type === 'text')?.text
  return text ? JSON.parse(text) : null
}
