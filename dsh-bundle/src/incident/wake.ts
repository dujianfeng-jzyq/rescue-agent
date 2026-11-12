/**
 * 唤醒核心（第三版 P3，docs/design-v3.md 7.3，验证清单 #24；不依赖 DSH，单独测试）：
 * 扫描事件库里“还没投递也没确认”的告警，按事件合并成一条消息，交给 deliver() 投递到值班会话，成功后写 delivered。
 *
 * - 投递是“至少一次”：flush 之后、写 delivered 之前崩溃会重复投递，消息里带告警编号，智能体按编号去重；
 * - 失败写 delivery_failed（带次数），按退避重试，超过次数放弃——告警仍是“未确认”，控制台继续显示；
 * - 消息正文只是事实和要求，判据不在这里；
 * - 值班会话正忙（在跑或队列里还有消息）时不投递，告警留在事件库里等它空闲（第五版，2026-11-10）：
 *   原来忙时也投递，消息排在 DSH 队列里，等轮到它时上一轮往往已用更新的数据处理过（演示里橙色告警排队，智能体答“重复投递”）。
 *   空闲后按当时的时间线判断（alertStatuses）：已被处理覆盖的告警不再投递，写 acked（by=plugin，带依据），只投新的。
 *   告警不会因此更晚被处理：排队的消息本来也要等当前这一轮结束。
 */

import { alertStatuses, type Entry, type IncidentStore, type IncidentView } from './store.ts'

export interface WakeOptions {
  /** 把消息投递到值班会话；成功返回会话编号，失败抛错 */
  deliver: (text: string, meta: { incidentId: string; alertIds: string[] }) => Promise<{ sessionId: string }>
  /** 失败后的重试退避（秒）；用完就放弃 */
  backoffS: number[]
  /** 当前真实时间（毫秒），测试可替换 */
  now?: () => number
  log?: (msg: string) => void
  /** 值班会话是否正忙（正在跑或队列里还有消息）；忙就先不投递。不给按不忙处理 */
  busy?: () => Promise<boolean>
}

const CAUSE_TEXT: Record<string, string> = {
  anomaly: '请立即对该工作面做完整研判（run_fire_assessment），把要点告知操作员；需要对外动作时按规则提交，由人确认。不要先问操作员“要不要研判”。',
  mine_alarm: '这是矿井自己的报警平台发来的越限报警（不是规程红线报警）。请对该工作面做完整研判（run_fire_assessment），把要点告知操作员；需要对外动作时按规则提交，由人确认。',
  followup_due: '这是你登记的跟踪计划到点了。请评估处置效果（review_effect），把结果告知操作员；效果不达预期时给出调整建议，对外动作仍由人确认。',
  approval_timeout: '你提交的对外动作等人工确认超时了，系统已提醒上一级并记入事件时间线。请告知操作员这条动作还在等确认、等了多久、已提醒谁；不要重复提交同一个动作，也不要绕过确认。',
  degraded: '监听器取数/检测连续失败，此期间的“无异常”不可信。请如实告知操作员监听已降级及其影响；不要用不可信的数据支撑对外动作。',
}

/**
 * 唤醒消息：只放还没被处理的告警（已被之后的处理覆盖的，投递前已记 acked、不进消息，见 Waker.drain）。
 * 原来还会给已处理的告警标“重复投递”，投递前过滤之后那段走不到，2026-11-11 代码复查第 3 轮删掉。
 */
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

  private async isBusy(): Promise<boolean> {
    if (!this.opts.busy) return false
    try {
      return await this.opts.busy()
    } catch (err) {
      // 查不到状态时照常投递（DSH 自己会排队），不能因此让告警卡住
      this.opts.log?.(`查询值班会话状态失败，照常投递：${(err as Error).message}`)
      return false
    }
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
        if (await this.isBusy()) continue
        const at = entries[entries.length - 1]!.at
        // 已被之后的研判、处置覆盖的告警（依据的数据不早于告警）不再投递：记 acked，写明依据
        const statuses = alertStatuses(entries, new Set(alerts.map((a) => a.event_id)))
        const covered = statuses.filter((st) => st.state === 'handled')
        for (const st of covered) {
          await this.store.append(view.incident_id, at, 'acked', 'plugin', { alert_id: st.alert_id, handled_by: st.handled_by, reason: '投递前已有依据不早于该告警的处理记录，不再投递' })
        }
        if (covered.length) this.opts.log?.(`事件 ${view.incident_id}：${covered.length} 条告警投递前已被处理，不再投递（${covered.map((c) => c.alert_id).join('、')}）`)
        const fresh = alerts.filter((a) => !covered.some((c) => c.alert_id === a.event_id))
        if (fresh.length === 0) continue
        const ids = fresh.map((a) => a.event_id)
        try {
          const { sessionId } = await this.opts.deliver(composeMessage(view, fresh), { incidentId: view.incident_id, alertIds: ids })
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
