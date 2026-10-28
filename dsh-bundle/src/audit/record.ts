/**
 * 审计记录的业务逻辑（不依赖 DSH）。
 *
 * DSH 会话日志本身已经是只追加的，并记录了 approval/asked、approval/decided 事件；
 * 这里另写一份“指令台账”，只收 actions 指令类工具的结果，方便复盘报告和对外审计直接读取。
 */

export interface ToolOutcome {
  callId: string
  name: string
  sessionId?: string | undefined
  arguments: unknown
  isError: boolean
  errorMessage?: string | undefined
  errorCode?: string | undefined
}

export interface AuditRecord {
  ts: string
  session: string | null
  call_id: string
  tool: string
  /** issued：已发布；denied：被红线预检、人工确认或 actions 服务拦下；failed：其他错误 */
  outcome: 'issued' | 'denied' | 'failed'
  detail: string | null
  arguments: unknown
}

const DENIAL_CODES = new Set(['RESCUE_RED_LINE', 'RESCUE_PRECHECK_UNAVAILABLE'])

/**
 * DSH 0.2.1-rc.4 的审批拒绝不带错误码，只能按消息识别（见 dsh-tools serviceAsk）。
 * actions 服务端红线拦截的消息以“指令未发布”开头。升级 DSH 时检查这里。
 */
const DENIAL_MESSAGES = [/the user rejected tool/, /approval for tool .* was cancelled/, /requires approval/, /指令未发布/]

export function toRecord(o: ToolOutcome, now: Date = new Date()): AuditRecord {
  let outcome: AuditRecord['outcome'] = 'issued'
  if (o.isError) {
    const denied = (o.errorCode !== undefined && DENIAL_CODES.has(o.errorCode)) || DENIAL_MESSAGES.some(re => re.test(o.errorMessage ?? ''))
    outcome = denied ? 'denied' : 'failed'
  }
  return {
    ts: now.toISOString(),
    session: o.sessionId ?? null,
    call_id: o.callId,
    tool: o.name,
    outcome,
    detail: o.isError ? (o.errorMessage ?? null) : null,
    arguments: o.arguments,
  }
}

export function toLine(r: AuditRecord): string {
  return `${JSON.stringify(r)}\n`
}
