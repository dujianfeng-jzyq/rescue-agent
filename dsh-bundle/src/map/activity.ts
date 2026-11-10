/**
 * 智能体的实时活动（docs/design-v4-map.md P3）：忙还是闲、正在调什么工具、有没有在等人确认或回答。
 * 来自 DSH 的事件（agent/status、tools/pre-execute 与 post-execute、approval/request、user-questions/request），
 * 只旁路观察、不改变任何事件的结果。纯逻辑，事件接线在 index.ts。
 *
 * 保护：事件可能漏（插件启动时智能体已经在跑；idle 事件没收到），所以每条记录有过期时间，
 * 过期的当作没有，宁可显示“待命”也不永远显示“工作中”；并且标明“已经收到过事件”（known），
 * 没收到过事件时界面要写“由事件库推断”，不冒充实时状态。
 */

import { policyOf } from './state.ts'

const TTL_MS = 15 * 60_000

/** 工具的中文说明：只列智能体常用的，其余不显示具体动作（只显示“工作中”）。 */
const TOOL_LABEL: Record<string, string> = {
  run_fire_assessment: '研判',
  check_mine: '检测矿井',
  draft_sealing_plan: '编制封闭方案',
  draft_firefighting_plan: '编制防灭火方案',
  get_response_options: '查处置选项',
  get_field_conditions: '查现场条件确认卡片',
  review_effect: '处置效果复评',
  review_timeline: '事件复盘',
  assess_unsealing: '启封评估',
  get_incident: '读取事件',
  open_incident: '开事件',
  set_incident_status: '更新事件状态',
  add_followup: '登记跟踪计划',
  complete_followup: '完成跟踪计划',
  close_incident: '关闭事件',
  map_view: '操作地图',
  kb_get_articles: '查规程条文',
  kb_search: '检索知识库',
  kb_list_datasets: '查知识库',
  ask_user_question: '向操作员提问',
  present: '交付文件',
}

export function toolLabel(name: string): string | null {
  if (TOOL_LABEL[name]) return TOOL_LABEL[name]!
  // MCP 的对外动作：取动作分级表里的名称，如 mcp__actions__publish_warning → 发布预警等级
  const short = name.startsWith('mcp__') ? name.split('__').slice(2).join('__') : name
  const pol = policyOf(short)
  if (pol && name.startsWith('mcp__actions__')) return `提交：${pol.name}`
  return null
}

interface Running {
  seen: number
  tools: Map<string, { name: string; at: number }>
}

export interface ActivitySnapshot {
  /** 收到过事件：没收到过时界面不能说“实时” */
  known: boolean
  /** 在忙的智能体数（含阶段子 Agent） */
  running: number
  /** 正在调用的工具的中文说明（去重，认识的才有） */
  doing: string[]
  waitingQuestions: number
  waitingApprovals: { tool: string; name: string }[]
}

export class AgentActivity {
  private readonly agents = new Map<string, Running>()
  private readonly questions = new Map<string, number>()
  private readonly approvals = new Map<string, { tool: string; at: number }>()
  private sawEvent = false
  private readonly now: () => number

  constructor(now: () => number = Date.now) {
    this.now = now
  }

  private touch(agentId: string): Running {
    this.sawEvent = true
    let a = this.agents.get(agentId)
    if (!a) this.agents.set(agentId, (a = { seen: 0, tools: new Map() }))
    a.seen = this.now()
    return a
  }

  status(agentId: string, status: 'idle' | 'running'): void {
    this.sawEvent = true
    if (status === 'idle') {
      this.agents.delete(agentId) // 闲了：它名下的工具调用也一并清掉（被拒绝的工具不会有 post-execute）
      return
    }
    this.touch(agentId)
  }

  dispose(agentId: string): void {
    this.agents.delete(agentId)
  }

  toolStart(callId: string, agentId: string, name: string): void {
    this.touch(agentId).tools.set(callId, { name, at: this.now() })
  }

  toolEnd(callId: string, agentId: string): void {
    const a = this.agents.get(agentId)
    if (a) {
      a.tools.delete(callId)
      a.seen = this.now()
    }
  }

  questionStart(id: string): void {
    this.sawEvent = true
    this.questions.set(id, this.now())
  }

  questionEnd(id: string): void {
    this.questions.delete(id)
  }

  approvalStart(id: string, tool: string): void {
    this.sawEvent = true
    this.approvals.set(id, { tool, at: this.now() })
  }

  approvalEnd(id: string): void {
    this.approvals.delete(id)
  }

  snapshot(): ActivitySnapshot {
    const t = this.now()
    const fresh = (at: number): boolean => t - at <= TTL_MS
    for (const [id, a] of this.agents) if (!fresh(a.seen)) this.agents.delete(id)
    for (const [id, at] of this.questions) if (!fresh(at)) this.questions.delete(id)
    for (const [id, a] of this.approvals) if (!fresh(a.at)) this.approvals.delete(id)
    const doing = new Set<string>()
    for (const a of this.agents.values()) for (const { name } of a.tools.values()) {
      const l = toolLabel(name)
      if (l) doing.add(l)
    }
    return {
      known: this.sawEvent,
      running: this.agents.size,
      doing: [...doing],
      waitingQuestions: this.questions.size,
      waitingApprovals: [...this.approvals.values()].map((a) => {
        const short = a.tool.startsWith('mcp__') ? a.tool.split('__').slice(2).join('__') : a.tool
        return { tool: short, name: policyOf(short)?.name ?? short }
      }),
    }
  }
}

const KEY = Symbol.for('rescue.map.activity')
/** 进程内共享的活动追踪器（与指令中心一样挂在 globalThis 上）。 */
export function getActivity(): AgentActivity {
  const g = globalThis as unknown as Record<symbol, AgentActivity | undefined>
  return (g[KEY] ??= new AgentActivity())
}
