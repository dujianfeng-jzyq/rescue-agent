/**
 * 固定流程经 DSH 工具管道（ctx.tools.execute）调用 MCP 工具后取结构化结果。
 *
 * 结果的 value 不一定就是工具返回的对象（rc.4 下 MCP 工具的 value 是另一层包装），所以按关键字段判断：
 * value 里有 key 就用它，否则解析第一个文本块的 JSON；都没有时抛错，不能当作“没有结果”处理
 * （P6 彩排：query_last_issued 的结果被当成 found=false，review_effect 误报“台账里没有已下发的方案”）。
 */
export function toolValue(r: { isError?: boolean; error?: { message?: string }; value?: unknown; content?: { type: string; text?: string }[] }, name: string, key: string): Record<string, any> {
  if (r.isError) throw new Error(r.error?.message ?? `${name} 调用失败`)
  const has = (v: unknown): v is Record<string, any> => Boolean(v) && typeof v === 'object' && !Array.isArray(v) && key in (v as object)
  if (has(r.value)) return r.value
  const text = (r.content ?? []).find((b) => b.type === 'text')?.text
  let v: unknown = null
  if (text) {
    try {
      v = JSON.parse(text)
    } catch {
      v = null
    }
  }
  if (!has(v)) throw new Error(`${name} 的结果里没有 ${key}`)
  return v
}
