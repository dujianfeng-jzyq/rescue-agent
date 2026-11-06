/**
 * rescue-incident：事件插件（第三版 P3，docs/design-v3.md 5.2、7.2）。给智能体读写事件的工具：
 * get_incident、open_incident、set_incident_status、add_followup、complete_followup、close_incident。
 * 事件库只由监听器（Python）和本插件写；记录（Record）仍只有证据插件写。
 *
 * 跟踪计划的到点时间由代码按矿井时钟算（add_followup 只收“多少矿井小时后”），到点由监听器写 followup_due 并唤醒值班会话。
 */

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

import { parse as parseYaml } from 'yaml'

import type { Context } from '../adapter/dsh.ts'
import { defineTool, Schema } from '../adapter/dsh.ts'
import { McpClient } from '../evidence/mcp-client.ts'
import { IncidentStore, STATUSES, type Entry, type IncidentView } from './store.ts'

export const name = 'rescue-incident'
export const inject = ['tools']

export interface Config {
  incidentDir: string
  mineDataUrl: string
  mineDataToken?: string
  mineDataTimeoutMs: number
  /** 技能目录：add_followup 的缺省间隔取自 fire-incident-management/references/followup.yaml */
  skillsDir: string
}

export const Config: Schema<Config> = Schema.object({
  incidentDir: Schema.string().required(),
  mineDataUrl: Schema.string().default('http://127.0.0.1:8101/mcp'),
  mineDataToken: Schema.string(),
  mineDataTimeoutMs: Schema.number().default(30_000),
  skillsDir: Schema.string().required(),
})

const jsonOutput = {
  schema: { type: 'json' as const },
  render: (_args: unknown, value: unknown) => [{ type: 'text' as const, text: JSON.stringify(value, null, 2) }],
}
const toJson = (v: unknown) => v as never

const TIMELINE_LIMIT = 40

const brief = (e: Entry) => ({ event_id: e.event_id, at: e.at, kind: e.kind, by: e.by, ...e.data })

export function mineMsPlusHours(now: string, hours: number): string {
  const t = Date.parse(`${now.slice(0, 19)}Z`) + Math.round(hours * 3_600_000)
  return new Date(t).toISOString().slice(0, 19)
}

export function apply(ctx: Context, config: Config): void {
  const store = new IncidentStore(config.incidentDir)
  const mine = new McpClient({ server: 'mine_data', url: config.mineDataUrl, token: config.mineDataToken, timeoutMs: config.mineDataTimeoutMs })
  registerTools(ctx as any, store, async () => String((await mine.call('get_clock', {})).now).slice(0, 19), () => followupParams(config.skillsDir))
}

export interface FollowupParams {
  default_after_h: number
  max_after_h: number
}

export async function followupParams(skillsDir: string): Promise<FollowupParams> {
  const y = parseYaml(await readFile(join(skillsDir, 'fire-incident-management', 'references', 'followup.yaml'), 'utf8'))
  for (const k of ['default_after_h', 'max_after_h'] as const) if (typeof y?.[k] !== 'number' || y[k] <= 0) throw new Error(`fire-incident-management/references/followup.yaml 缺少 ${k}`)
  return { default_after_h: y.default_after_h, max_after_h: y.max_after_h }
}

/** 注册工具（clock 返回矿井时间；单独成函数方便不起 DSH 测试） */
export function registerTools(c: { tools: { register: (t: unknown) => void } }, store: IncidentStore, clock: () => Promise<string>, followups: () => Promise<FollowupParams>): void {
  const view = async (id: string, withTimeline: boolean) => {
    const v: IncidentView = await store.get(id)
    if (!withTimeline) return v
    const entries = await store.entries(id)
    const want = new Set(v.pending_alerts)
    return {
      ...v,
      pending_alert_details: entries.filter((e) => want.has(e.event_id)).map(brief),
      timeline: entries.slice(-TIMELINE_LIMIT).map(brief),
      timeline_truncated: entries.length > TIMELINE_LIMIT,
    }
  }

  c.tools.register(
    defineTool({
      name: 'get_incident',
      description:
        '读事件。不给 incident_id：列出所有未关闭的事件（含待确认告警数、跟踪计划数）。给 incident_id：返回事件的状态、待确认告警、跟踪计划和最近的时间线。' +
        '事件由常驻监听器在发现异常时创建，之后的研判、对外动作、跟踪都应挂在事件上。',
      parameters: { incident_id: { type: 'string', description: '事件编号（INC-…）；缺省为列表' } },
      output: jsonOutput,
      execute: async (args: { incident_id?: string }) => {
        if (args.incident_id) return toJson(await view(String(args.incident_id), true))
        const open = await store.list()
        return toJson({ incidents: open.map((v) => ({ ...v, pending_alert_count: v.pending_alerts.length })), count: open.length })
      },
    }),
  )

  c.tools.register(
    defineTool({
      name: 'open_incident',
      description: '为某工作面开一个火情事件（监听器已为该工作面开了未关闭的事件时，返回已有的而不是新开）。用于操作员口头报告异常、监听器尚未发现的情形。',
      parameters: {
        panel: { type: 'string', required: true, description: '工作面编号' },
        summary: { type: 'string', required: true, description: '一句话说明，照实写操作员报告的内容' },
      },
      output: jsonOutput,
      execute: async (args: { panel: string; summary: string }) => {
        const existing = await store.findOpen('fire', String(args.panel))
        if (existing) return toJson({ opened: false, incident: existing })
        const id = await store.open(await clock(), { category: 'fire', panel: String(args.panel), summary: String(args.summary), by: 'agent' })
        return toJson({ opened: true, incident: await store.get(id) })
      },
    }),
  )

  c.tools.register(
    defineTool({
      name: 'set_incident_status',
      description: '更新事件状态：monitoring 监测中、handling 处置中（已开始处置）、stable 已稳定。关闭事件用 close_incident。',
      parameters: {
        incident_id: { type: 'string', required: true, description: '事件编号' },
        status: { type: 'string', required: true, enum: ['monitoring', 'handling', 'stable'], description: '新状态' },
        reason: { type: 'string', required: true, description: '一句话原因' },
      },
      output: jsonOutput,
      execute: async (args: { incident_id: string; status: string; reason: string }) => {
        if (!STATUSES.includes(args.status as never) || args.status === 'closed') throw new Error('status 只能是 monitoring、handling、stable')
        await store.append(String(args.incident_id), await clock(), 'status', 'agent', { status: args.status, reason: String(args.reason) })
        return toJson(await view(String(args.incident_id), false))
      },
    }),
  )

  c.tools.register(
    defineTool({
      name: 'add_followup',
      description:
        '登记跟踪计划：处置下发后，过一段矿井时间到点，监听器会唤醒你复评（review_effect）。到点时间由系统按矿井时钟算，你不要自己算。' +
        'after_h 不给就用技能规定的默认间隔（返回里照抄）；指挥人员明确要求某个间隔时才填。expect 写一句话预期（如“注惰后上隅角 CO 应回落”）。',
      parameters: {
        incident_id: { type: 'string', required: true, description: '事件编号' },
        after_h: { type: 'number', description: '多少矿井小时后复评（大于 0）；缺省取技能规定的默认间隔' },
        expect: { type: 'string', required: true, description: '一句话预期' },
      },
      output: jsonOutput,
      execute: async (args: { incident_id: string; after_h?: number; expect: string }) => {
        const p = await followups()
        const hours = args.after_h === undefined ? p.default_after_h : Number(args.after_h)
        if (!Number.isFinite(hours) || hours <= 0 || hours > p.max_after_h) throw new Error(`after_h 应大于 0 且不超过 ${p.max_after_h} 个矿井小时`)
        const now = await clock()
        const dueAt = mineMsPlusHours(now, hours)
        const followupId = `FU-${now.slice(0, 19).replace(/[-:]/g, '')}-${Math.random().toString(36).slice(2, 6)}`
        await store.append(String(args.incident_id), now, 'followup_added', 'agent', { followup_id: followupId, due_at: dueAt, expect: String(args.expect) })
        return toJson({ followup_id: followupId, due_at: dueAt, now, after_h: hours })
      },
    }),
  )

  c.tools.register(
    defineTool({
      name: 'complete_followup',
      description: '跟踪计划已完成（复评做完）时标记。result 写一句话结论，照抄工具结果。',
      parameters: {
        incident_id: { type: 'string', required: true, description: '事件编号' },
        followup_id: { type: 'string', required: true, description: '跟踪计划编号（FU-…）' },
        result: { type: 'string', required: true, description: '一句话结论' },
      },
      output: jsonOutput,
      execute: async (args: { incident_id: string; followup_id: string; result: string }) => {
        const v = await store.get(String(args.incident_id))
        if (!v.followups.some((f) => f.followup_id === args.followup_id)) throw new Error(`事件 ${v.incident_id} 没有跟踪计划 ${args.followup_id}`)
        await store.append(v.incident_id, await clock(), 'followup_done', 'agent', { followup_id: String(args.followup_id), result: String(args.result) })
        return toJson(await view(v.incident_id, false))
      },
    }),
  )

  c.tools.register(
    defineTool({
      name: 'close_incident',
      description: '关闭事件。有未完成的跟踪计划或未确认的告警时拒绝，先处理完。',
      parameters: {
        incident_id: { type: 'string', required: true, description: '事件编号' },
        reason: { type: 'string', required: true, description: '一句话原因' },
      },
      output: jsonOutput,
      execute: async (args: { incident_id: string; reason: string }) => {
        const v = await store.get(String(args.incident_id))
        const open = v.followups.filter((f) => f.state !== 'done')
        if (open.length) throw new Error(`还有 ${open.length} 个未完成的跟踪计划（${open.map((f) => f.followup_id).join('、')}），先复评并 complete_followup`)
        if (v.pending_alerts.length) throw new Error(`还有 ${v.pending_alerts.length} 条未确认的告警，先处理`)
        await store.append(v.incident_id, await clock(), 'status', 'agent', { status: 'closed', reason: String(args.reason) })
        return toJson(await view(v.incident_id, false))
      },
    }),
  )
}
