/**
 * 人工确认与红线预检的业务逻辑（不依赖 DSH，可独立测试）。
 *
 * 约定：所有需要人工确认的指令都在 actions MCP 服务里，DSH 中的工具名为 `mcp__actions__<工具名>`，
 * 该服务的每个工具都需要确认（只读自查工具放在 kb 服务）。exemptPrefixes 留作以后需要例外时使用。
 */

export interface ApprovalConfig {
  /** 需要确认的 MCP 服务名（dsh-mcp-client 的 serverName）。 */
  gatedServer: string
  /** 免确认的工具名前缀。 */
  exemptPrefixes: string[]
  /** actions 服务的红线预检接口。 */
  precheckUrl: string
  precheckTimeoutMs: number
  /** 工具名 → 确认人角色，用于确认提示。 */
  confirmers: Record<string, string>
  /** actions 服务的令牌（与 RESCUE_MCP_TOKEN 一致）；为空则不带认证头。 */
  token?: string | undefined
}

export const DEFAULT_CONFIRMERS: Record<string, string> = {
  publish_warning: '通风部门负责人',
  issue_firefighting_plan: '总工程师',
  escalate_emergency: '总工程师或矿长',
  notify_evacuation: '调度员',
  issue_dispatch_order: '指挥部指定负责人',
  issue_sealing_plan: '总指挥 + 救护队带队指挥员',
  approve_unsealing: '总指挥',
}

export const ACTION_NAMES: Record<string, string> = {
  publish_warning: '发布预警等级',
  issue_firefighting_plan: '下发防灭火方案',
  escalate_emergency: '升级为应急响应',
  notify_evacuation: '发布补充撤人名单',
  issue_dispatch_order: '下发物资调度单',
  issue_sealing_plan: '下发封闭方案',
  approve_unsealing: '批准启封',
}

export type Gate = { gated: false } | { gated: true; rawName: string }

/** 与 DSH PreToolDecision 结构一致的子集，胶水代码直接返回。 */
export type Verdict =
  | { kind: 'deny'; reason: string; code: string }
  | { kind: 'ask'; reason: string; displayReason: { en: string; zh: string } }

export interface PrecheckResponse {
  ok: boolean
  problems: string[]
  /** 给确认人看的说明（如预警等级与研判快照不同时的人工理由）。 */
  notes?: string[]
}

export type FetchLike = (url: string, init: { method: string; headers: Record<string, string>; body: string; signal: AbortSignal }) => Promise<{ ok: boolean; status: number; json(): Promise<unknown> }>

export function classify(toolName: string, config: Pick<ApprovalConfig, 'gatedServer' | 'exemptPrefixes'>): Gate {
  const prefix = `mcp__${config.gatedServer}__`
  if (!toolName.startsWith(prefix)) return { gated: false }
  const rawName = toolName.slice(prefix.length)
  if (config.exemptPrefixes.some(p => rawName.startsWith(p))) return { gated: false }
  return { gated: true, rawName }
}

export function askVerdict(rawName: string, config: Pick<ApprovalConfig, 'confirmers'>, notes: string[] = []): Verdict {
  const who = config.confirmers[rawName] ?? '指挥部授权人员'
  const what = ACTION_NAMES[rawName] ?? rawName
  const extra = notes.length ? `\n请注意：${notes.join('；')}` : ''
  return {
    kind: 'ask',
    reason: `${what}：需由【${who}】确认后执行（红线预检已通过）${extra}`,
    displayReason: {
      en: `${what} requires confirmation by ${who}. Red-line pre-check passed.${notes.length ? ` Note: ${notes.join('; ')}` : ''}`,
      zh: `${what}：需由【${who}】确认后执行。红线预检已通过。${extra}`,
    },
  }
}

/**
 * 请求 actions 服务预检。任何异常都按“拒绝”处理（fail closed）：
 * 预检服务不可用时，宁可拦下指令，也不能让未经校验的方案进入人工确认。
 */
export async function precheck(
  rawName: string,
  args: unknown,
  config: ApprovalConfig,
  signal: AbortSignal,
  fetchImpl: FetchLike,
): Promise<Verdict> {
  const timeout = AbortSignal.timeout(config.precheckTimeoutMs)
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (config.token) headers.Authorization = `Bearer ${config.token}`
  let body: PrecheckResponse
  try {
    const res = await fetchImpl(config.precheckUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify({ tool: rawName, arguments: args }),
      signal: AbortSignal.any([signal, timeout]),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    body = (await res.json()) as PrecheckResponse
    if (typeof body?.ok !== 'boolean' || !Array.isArray(body.problems)) throw new Error('预检返回格式不正确')
  } catch (err) {
    return {
      kind: 'deny',
      code: 'RESCUE_PRECHECK_UNAVAILABLE',
      reason: `红线预检服务不可用，指令已拦截（${(err as Error).message}）。请检查 actions 服务后重试，紧急情况按纸质预案处置。`,
    }
  }
  if (!body.ok) {
    return { kind: 'deny', code: 'RESCUE_RED_LINE', reason: `红线预检未通过，指令已拦截：\n${body.problems.join('\n')}` }
  }
  const notes = Array.isArray(body.notes) ? body.notes.filter((n): n is string => typeof n === 'string') : []
  return askVerdict(rawName, config, notes)
}
