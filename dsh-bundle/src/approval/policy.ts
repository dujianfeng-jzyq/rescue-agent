/**
 * 人工确认与红线预检的业务逻辑（不依赖 DSH，可独立测试）。
 *
 * 约定：所有对外动作都在 guard（actions MCP 服务）里，DSH 中的工具名为 `mcp__actions__<工具名>`。
 * 哪些工具要确认由动作分级表决定（契约 rescue_contract.action_policy，导出为 data/action-policy.json）：
 * C 级（对外动作）弹确认，A、B 级（只读、内部提示）不弹；**没有登记的工具按 C 级处理**（fail closed）。
 * 预检返回 ApprovalSummary（契约 0.8.0）时，确认提示按它列出要做什么、引用的记录、逐项核对结果和需要确认人把关的事项。
 */

export type ActionLevel = 'A' | 'B' | 'C'

/** 动作分级表的一行（rescue_contract.action_policy.ActionPolicy） */
export interface ActionPolicy {
  tool: string
  level: ActionLevel
  /** 中文动作名（弹窗标题） */
  name: string
  /** 确认人角色（C 级） */
  confirmer?: string | null
  /** 批准超时（分钟）：P3 的监听器据此提醒、升级 */
  approval_timeout_min?: number | null
  escalate_to?: string | null
}

export interface ApprovalConfig {
  /** 需要确认的 MCP 服务名（dsh-mcp-client 的 serverName）。 */
  gatedServer: string
  /** 动作分级表（来自契约的导出）。 */
  policies: ActionPolicy[]
  /** guard 服务的红线预检接口。 */
  precheckUrl: string
  precheckTimeoutMs: number
  /** guard 服务的令牌（与 RESCUE_MCP_ACTIONS_TOKEN 一致）；为空则不带认证头。 */
  token?: string | undefined
}

/** 工具的分级：没有登记的按 C 级（fail closed）。 */
export function levelOf(rawName: string, policies: ActionPolicy[]): ActionLevel {
  return policies.find((p) => p.tool === rawName)?.level ?? 'C'
}

export type Gate = { gated: false } | { gated: true; rawName: string }

/** 与 DSH PreToolDecision 结构一致的子集，胶水代码直接返回。 */
export type Verdict =
  | { kind: 'deny'; reason: string; code: string }
  | { kind: 'ask'; reason: string; displayReason: { en: string; zh: string } }

/** 契约 ApprovalSummary（rescue_contract.models），由 actions 预检生成 */
export interface ApprovalSummary {
  action: string
  tool: string
  record_ids: string[]
  checks: { name: string; ok: boolean; detail?: string | null }[]
  human_items: string[]
  override_reason?: string | null
}

export interface PrecheckResponse {
  ok: boolean
  problems: string[]
  /** 给确认人看的说明（如预警等级与研判快照不同时的人工理由）。 */
  notes?: string[]
  summary?: ApprovalSummary
}

export type FetchLike = (url: string, init: { method: string; headers: Record<string, string>; body: string; signal: AbortSignal }) => Promise<{ ok: boolean; status: number; json(): Promise<unknown> }>

export function classify(toolName: string, config: Pick<ApprovalConfig, 'gatedServer' | 'policies'>): Gate {
  const prefix = `mcp__${config.gatedServer}__`
  if (!toolName.startsWith(prefix)) return { gated: false }
  const rawName = toolName.slice(prefix.length)
  if (levelOf(rawName, config.policies) !== 'C') return { gated: false }
  return { gated: true, rawName }
}

/** 确认提示的正文：按 ApprovalSummary 逐项列出（不透出工具原始参数） */
export function summaryText(s: ApprovalSummary): string {
  const lines: string[] = []
  if (s.record_ids.length) lines.push(`依据记录：${s.record_ids.join('、')}`)
  for (const c of s.checks) lines.push(`${c.ok ? '✓' : '✗'} ${c.name}${c.detail ? `：${c.detail}` : ''}`)
  if (s.human_items.length) lines.push(`请确认人把关：${s.human_items.join('；')}`)
  return lines.join('\n')
}

function isSummary(v: unknown): v is ApprovalSummary {
  const s = v as ApprovalSummary
  return !!s && typeof s.action === 'string' && Array.isArray(s.checks) && Array.isArray(s.record_ids) && Array.isArray(s.human_items)
}

export function askVerdict(rawName: string, config: Pick<ApprovalConfig, 'policies'>, notes: string[] = [], summary?: ApprovalSummary): Verdict {
  const policy = config.policies.find((p) => p.tool === rawName)
  const who = policy?.confirmer ?? '指挥部授权人员'
  if (summary) {
    const body = summaryText(summary)
    return {
      kind: 'ask',
      reason: `${summary.action}：需由【${who}】确认后执行（红线预检已通过）\n${body}`,
      displayReason: {
        en: `${summary.action} requires confirmation by ${who}. Pre-check passed.\n${body}`,
        zh: `${summary.action}：需由【${who}】确认后执行。红线预检已通过。\n${body}`,
      },
    }
  }
  const what = policy?.name ?? rawName
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
  return askVerdict(rawName, config, notes, isSummary(body.summary) ? body.summary : undefined)
}
