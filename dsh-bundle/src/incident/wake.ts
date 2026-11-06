/**
 * 唤醒核心（第三版 P3，docs/design-v3.md 7.3，验证清单 #24；不依赖 DSH，单独测试）：
 * 扫描事件库里“还没投递也没确认”的告警，按事件合并成一条消息，交给 deliver() 投递到值班会话，成功后写 delivered。
 *
 * - 投递是“至少一次”：flush 之后、写 delivered 之前崩溃会重复投递，消息里带告警编号，智能体按编号去重；
 * - 失败写 delivery_failed（带次数），按退避重试，超过次数放弃——告警仍是“未确认”，控制台继续显示；
 * - 消息正文只是事实和要求，判据不在这里。
 */

import type { Entry, IncidentStore, IncidentView } from './store.ts'

export interface WakeOptions {
  /** 把消息投递到值班会话；成功返回会话编号，失败抛错 */
  deliver: (text: string, meta: { incidentId: string; alertIds: string[] }) => Promise<{ sessionId: string }>
  /** 失败后的重试退避（秒）；用完就放弃 */
  backoffS: number[]
  /** 当前真实时间（毫秒），测试可替换 */
  now?: () => number
  log?: (msg: string) => void
}

const CAUSE_TEXT: Record<string, string> = {
  anomaly: '请立即对该工作面做完整研判（run_fire_assessment），把要点告知操作员；需要对外动作时按规则提交，由人确认。不要先问操作员“要不要研判”。',
  mine_alarm: '这是矿井自己的报警平台发来的报警。请先做一次风险检测（check_mine，mode=once），异常则立即做完整研判，并把结果告知操作员。',
  followup_due: '这是你登记的跟踪计划到点了。请评估处置效果（review_effect），把结果告知操作员；效果不达预期时给出调整建议，对外动作仍由人确认。',
  approval_timeout: '你提交的对外动作等人工确认超时了，系统已提醒上一级并记入事件时间线。请告知操作员这条动作还在等确认、等了多久、已提醒谁；不要重复提交同一个动作，也不要绕过确认。',
  degraded: '监听器取数/检测连续失败，此期间的“无异常”不可信。请如实告知操作员监听已降级及其影响；不要用不可信的数据支撑对外动作。',
}

export function composeMessage(view: IncidentView, alerts: Entry[]): string {
  const head = `【值守事件 ${view.incident_id}】${view.panel ?? '全矿'}（${view.category === 'monitor' ? '监听降级' : '火情'}，${view.status_name}）。以下告警由监听器发现，不经模型：`
  const lines = alerts.map((a) => `- 告警 ${a.event_id}（矿井时间 ${a.at}）：${a.data.summary ?? ''}`)
  const causes = [...new Set(alerts.map((a) => String(a.data.cause)))]
  const todo = causes.map((c) => CAUSE_TEXT[c] ?? '请核实并告知操作员。').join('\n')
  return `${head}\n${lines.join('\n')}\n${todo}\n（投递可能重复：告警编号已处理过的直接忽略。用 get_incident 查看事件时间线。）`
}

export class Waker {
  private busy = false
  private readonly store: IncidentStore
  private readonly opts: WakeOptions
  constructor(store: IncidentStore, opts: WakeOptions) {
    this.store = store
    this.opts = opts
  }

  /** 处理所有待投递告警；返回本次成功投递的告警编号 */
  async drain(): Promise<string[]> {
    if (this.busy) return []
    this.busy = true
    const delivered: string[] = []
    try {
      for (const view of await this.store.list()) {
        if (view.pending_alerts.length === 0) continue
        const entries = await this.store.entries(view.incident_id)
        const want = new Set(view.pending_alerts)
        const tries = (id: string) => entries.filter((e) => e.kind === 'delivery_failed' && e.data.alert_id === id).length
        // 已放弃（失败次数用完）的告警保持“未确认”，不再投递；新的告警照常投递
        const alerts = entries.filter((e) => e.kind === 'alert' && want.has(e.event_id) && tries(e.event_id) <= this.opts.backoffS.length)
        if (alerts.length === 0) continue
        const attempts = Math.max(...alerts.map((a) => tries(a.event_id)))
        if (attempts > 0) {
          const lastFail = [...entries].reverse().find((e) => e.kind === 'delivery_failed')!
          const wait = this.opts.backoffS[attempts - 1]! * 1000
          if ((this.opts.now?.() ?? Date.now()) < Date.parse(lastFail.wall) + wait) continue
        }
        const at = entries[entries.length - 1]!.at
        const ids = alerts.map((a) => a.event_id)
        try {
          const { sessionId } = await this.opts.deliver(composeMessage(view, alerts), { incidentId: view.incident_id, alertIds: ids })
          for (const id of ids) await this.store.append(view.incident_id, at, 'delivered', 'plugin', { alert_id: id, session_id: sessionId })
          delivered.push(...ids)
        } catch (err) {
          const n = attempts + 1
          this.opts.log?.(`投递事件 ${view.incident_id} 失败（第 ${n} 次）：${(err as Error).message}`)
          for (const id of ids) await this.store.append(view.incident_id, at, 'delivery_failed', 'plugin', { alert_id: id, error: String((err as Error).message).slice(0, 300), attempts: n })
          if (n > this.opts.backoffS.length) this.opts.log?.(`事件 ${view.incident_id} 的告警投递 ${n} 次失败，放弃；告警仍未确认`)
        }
      }
    } finally {
      this.busy = false
    }
    return delivered
  }
}
