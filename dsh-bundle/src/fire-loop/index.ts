/**
 * rescue-fire-loop：火灾闭环的固定流程插件（docs/design-v2.md 4.4，ADR 0007）。run_fire_assessment（P1）、draft_sealing_plan（P3）、review_effect（P4）。
 *
 * 进程内调用证据插件的服务 rescueEvidence（采集、运行技能脚本、渲染报告）和知识库插件的 rescueKnowledge（条文原文），
 * 所以必须和这两个插件放在同一个带 isolate 的 cordis:group 里（profiles/v2.patch.yml）。步骤在 assessment.ts，判据全在技能脚本里。
 *
 * 阶段“研判说明”用 ctx.subagents.startActivation 起子 Agent（与调用方同一组合，delivery: caller，outputSchema），
 * 用 tools.guard 限制它只能调 structured_output（子 Agent 的编号先预留，守卫从第一次调用起就生效；
 * 委派出的子 Agent 审批策略为 never，即使守卫没拦住，actions 也会被自动拒绝，验证清单 #19）。
 * 报告写进会话工作区，用 present 交付（报告和风险图）。concludeTurn 默认不开：rc.4 网页端在工具结束本轮时
 * 只显示折叠的“Completed”，看不到报告和交付卡片（2026-11-03 截图核对），所以仍由主预设把报告原样放进回答。
 * 运行过程写 tool-workflow/* 事件，网页端在工具调用下显示运行节点（#19）。
 *
 * draft_sealing_plan 的红线预检调 actions 的 precheck_plan（ctx.tools.execute，经 DSH 工具管道：审计照常，审批插件按
 * precheck_ 前缀放行）；两个阶段“方案编写”“合规审核”用同一个 StageRunner。步骤在 sealing.ts，阶段逻辑在 plan-stages.ts。
 */

import { randomUUID } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

import type { Context } from '../adapter/dsh.ts'
import { defineTool, Schema } from '../adapter/dsh.ts'
import type RescueEvidence from '../evidence/index.ts'
import type RescueKnowledge from '../knowledge/index.ts'
import { FLOW, runFireAssessment } from './assessment.ts'
import { STAGE, type StageRunner } from './explanation.ts'
import { draftFirefightingPlan, draftSealingPlan, FIREFIGHTING, type HumanInput, type PlanFlow, type Redline, SEALING } from './sealing.ts'
import { IncidentStore } from '../incident/store.ts'
import { noteOnIncident } from '../incident/timeline.ts'
import { UNSEAL_FLOW, assessUnsealing } from './unsealing.ts'
import { TIMELINE_FLOW, TimelineInputError, reviewTimeline } from './timeline.ts'
import { EFFECT_FLOW, EffectInputError, type LastIssued, reviewEffect } from './effect.ts'

export const name = 'rescue-fire-loop'
export const inject = ['tools', 'rescueEvidence', 'rescueKnowledge', 'workingDirectory', 'subagents']

export interface Config {
  /** 起阶段子 Agent 的后端（本机进程内） */
  stageProvider: string
  /** 阶段子 Agent 的时限（秒）；超时判阶段失败，报告只含事实部分 */
  stageTimeoutS: number
  /** 关掉阶段“研判说明”（报告只含事实部分） */
  disableStage: boolean
  /** 交付报告后结束本轮（concludeTurn）。rc.4 网页端此时看不到报告，默认关 */
  concludeTurn: boolean
  /** draft_sealing_plan：方案编写与合规审核的轮数上限（占位 2，design-v2 第 10 节第 3 条） */
  sealingMaxRounds: number
  /** 红线预检的工具名（actions 的 precheck_plan，dsh-mcp-client 的 serverName 为 actions） */
  precheckTool: string
  /** 台账查询的工具名（actions 的 query_last_issued；review_effect 缺处置开始时间时用） */
  lastIssuedTool: string
  /** 事件库目录；给了就把研判、复评、封闭方案的结果自动记进该工作面未关闭事件的时间线（第三版 P4） */
  incidentDir?: string
}

export const Config: Schema<Config> = Schema.object({
  stageProvider: Schema.string().default('spawn'),
  stageTimeoutS: Schema.number().default(120),
  disableStage: Schema.boolean().default(false),
  concludeTurn: Schema.boolean().default(false),
  sealingMaxRounds: Schema.natural().min(1).default(2),
  precheckTool: Schema.string().default('mcp__actions__precheck_plan'),
  lastIssuedTool: Schema.string().default('mcp__actions__query_last_issued'),
  incidentDir: Schema.string(),
})

const STRUCTURED_OUTPUT = 'structured_output'
const toJson = (v: unknown) => JSON.parse(JSON.stringify(v)) as never

export function apply(ctx: Context, config: Config) {
  const c = ctx as any
  const evidence = (c.rescueEvidence as RescueEvidence).evidence
  const knowledge = (c.rescueKnowledge as RescueKnowledge).knowledge
  const stageChildren = new Set<string>()
  const incidents = config.incidentDir ? new IncidentStore(config.incidentDir) : undefined
  const logToIncident = async (panel: string, type: string, out: any) => {
    try {
      const id = await noteOnIncident(incidents, panel, { type, text: String(out.card?.summary ?? ''), record_ids: out.card?.record_ids ?? [] }, async () => String((await (c.rescueEvidence as RescueEvidence).mineData.call('get_clock', {})).now).slice(0, 19))
      if (id) out.incident_id = id
    } catch (err) {
      ctx.logger.warn(`${FLOW}: 记入事件时间线失败：${(err as Error).message}`)
    }
  }

  // 阶段子 Agent 只能提交结构化结果
  c.tools.guard((e: any) => {
    const ids = [e.agent?.id, e.agent?.session?.id].filter(Boolean).map(String)
    if (ids.some((i) => stageChildren.has(i)) && e.name !== STRUCTURED_OUTPUT) return `固定流程的阶段只能用 ${STRUCTURED_OUTPUT} 提交结果，不能调用 ${e.name}`
    return undefined
  })

  const append = (session: any, type: string, data: unknown) => {
    try {
      session.append(type, data)
    } catch (err) {
      ctx.logger.warn(`${FLOW}: 写会话事件 ${type} 失败：${(err as Error).message}`)
    }
  }

  function stageRunner(parent: any, runId: string): StageRunner {
    let seq = 0
    return async ({ label, prompt, schema, signal }) => {
      const n = ++seq
      const childId = randomUUID()
      stageChildren.add(childId)
      const timeout = AbortSignal.timeout(config.stageTimeoutS * 1000)
      const sig = signal ? AbortSignal.any([signal, timeout]) : timeout
      let activation: any
      try {
        activation = await c.subagents.startActivation({
          provider: config.stageProvider,
          label,
          childId,
          delivery: 'caller',
          signal: sig,
          request: { parent, outputSchema: schema, prompt: [{ type: 'text', text: prompt }] },
        })
        append(parent.session, 'tool-workflow/agent-start', { runId, seq: n, label, phase: label, childId: String(activation.childId) })
        const res = await Promise.race([
          activation.result,
          new Promise<never>((_, reject) => sig.addEventListener('abort', () => reject(new Error(timeout.aborted ? `超过 ${config.stageTimeoutS} 秒` : '已取消')), { once: true })),
        ])
        const ok = res?.stopReason === 'completed' && res?.structured !== undefined
        append(parent.session, 'tool-workflow/agent-end', { runId, seq: n, outcome: ok ? 'completed' : 'failed' })
        return ok ? { ok: true, structured: res.structured, childId } : { ok: false, error: `子 Agent 没有给出结构化结果（${res?.stopReason ?? '未知'}）`, childId }
      } catch (err) {
        append(parent.session, 'tool-workflow/agent-end', { runId, seq: n, outcome: 'failed' })
        return { ok: false, error: `子 Agent 运行失败：${(err as Error).message}`, childId }
      } finally {
        await activation?.dispose?.().catch((err: Error) => ctx.logger.warn(`${FLOW}: 释放子 Agent 失败：${err.message}`))
        stageChildren.delete(childId)
      }
    }
  }

  c.tools.register(
    defineTool({
      name: FLOW,
      description:
        '工作面采空区自燃的完整研判（固定流程，研判、要不要升级、有没有爆炸危险一律先调它）：按固定步骤采集证据，' +
        '计算实时红线报警、预警等级、自燃发展阶段、触发指标的趋势、全部束管测点的爆炸三角形（取最不利，附风险图）、' +
        '受威胁人员（等级达到阈值或 include_personnel=true 时）和升级建议，每步存为记录，再由“研判说明”阶段从清单里选出关注点和下一步。' +
        '完整报告和风险图已由本工具交付（网页端点开在右侧预览，不要再调用 present）；回答时把 brief_markdown（研判要点，含风险图）原样单独成段放进回答，不要改写、不要贴完整报告、不要另写分析与建议，回答到此结束。' +
        '返回 card（结果卡片）、brief_markdown、record_ids、result；发布预警、升级响应时引用 record_ids 里的记录编号。阈值和判据为开发占位值。',
      parameters: {
        panel: { type: 'string', required: true, description: '工作面编号，如 1201' },
        include_personnel: { type: 'boolean', description: '等级未达到阈值时也计算受威胁人员' },
      },
      output: {
        schema: { type: 'json' as const },
        // 模型看到的：结论摘要、提醒、报告、记录编号和结论；卡片的其余部分和流程事件只留在结果值里（不缩进，少占 token）
        render: (_args: unknown, v: any) => [
          {
            type: 'text' as const,
            text: JSON.stringify({ summary: v.card.summary, warnings: v.card.warnings, brief_markdown: v.brief_markdown, record_ids: v.record_ids, result: v.result, delivered: v.delivered }),
          },
        ],
      },
      execute: async (args: { panel: string; include_personnel?: boolean }, exec: any) => {
        const parent = exec.agent
        if (!parent) throw new Error(`${FLOW} 需要调用方会话`)
        const workspace: string = await c.workingDirectory.ensure(parent, exec.signal)
        const session = String(parent.id)
        const runId = `${FLOW}-${randomUUID()}`
        append(parent.session, 'tool-workflow/run-start', { runId, name: `${args.panel} 工作面研判` })
        let stopReason = 'completed'
        try {
          const out: any = await runFireAssessment(evidence, {
            panel: String(args.panel),
            includePersonnel: Boolean(args.include_personnel),
            origin: { channel: 'web', trigger: 'chat', caller: null, conversation_id: null },
            signal: exec.signal,
            // 风险图导出到会话工作区（records/<会话>/），网页端经 /api/file 显示
            chartPath: async (id) => (await evidence.exportRecord(id, workspace, session)).files[0] ?? null,
            ...(config.disableStage ? {} : { stage: stageRunner(parent, runId) }),
            getArticles: (document, articles) => knowledge.getArticles(document, articles).results,
          })

          // 交付：报告写成 Markdown 文件，连同风险图用 present 交付
          const dir = join(workspace, 'records', session)
          await mkdir(dir, { recursive: true })
          const reportPath = join(dir, `${out.card.record_ids[0]}.md`)
          await writeFile(reportPath, `${out.report_markdown}\n`, 'utf8')
          const files = [{ path: reportPath, description: `${args.panel} 工作面研判报告` }]
          const chart = out.result.explosion.chart_markdown as string | null
          if (chart) files.push({ path: chart.slice(chart.indexOf('](') + 2, -1), description: '爆炸三角形风险图（最不利测点）' })
          const presented = await c.tools.execute({
            callId: `${exec.callId}:present`,
            rootCallId: exec.rootCallId,
            name: 'present',
            arguments: { files },
            agent: parent,
            parent: exec.token,
            signal: exec.signal,
          })
          await logToIncident(String(args.panel), 'assessment', out)
          out.delivered = presented.isError
            ? { ok: false, error: presented.error?.message ?? '交付失败' }
            : { ok: true, files: files.map((f) => f.path), note: '报告文件和风险图已交付给用户，不要再调用 present' }
          if (!presented.isError && config.concludeTurn) exec.concludeTurn()
          return toJson(out)
        } catch (err) {
          stopReason = 'failed'
          throw err
        } finally {
          append(parent.session, 'tool-workflow/run-end', { runId, stopReason })
        }
      },
    }),
  )

  /** actions 的 precheck_plan：经 DSH 工具管道调用（调用方 Agent 必须挂了 actions） */
  async function precheck(exec: any, plan: Record<string, unknown>, n: number, tool: string): Promise<Redline> {
    const r = await c.tools.execute({
      callId: `${exec.callId}:precheck:${n}`,
      rootCallId: exec.rootCallId,
      name: config.precheckTool,
      arguments: { tool, plan },
      agent: exec.agent,
      parent: exec.token,
      signal: exec.signal,
    })
    if (r.isError) throw new Error(r.error?.message ?? `${config.precheckTool} 调用失败`)
    let v: any = r.value
    if (!v || typeof v !== 'object' || typeof v.ok !== 'boolean') {
      const text = (r.content ?? []).find((b: any) => b.type === 'text')?.text
      v = text ? JSON.parse(text) : null
    }
    if (!v || typeof v.ok !== 'boolean') throw new Error(`${config.precheckTool} 的结果里没有 ok`)
    return { ok: v.ok, problems: (v.problems ?? []).map(String), notes: (v.notes ?? []).map(String) }
  }

  const registerPlanFlow = (flow: PlanFlow, description: string, parameters: any, draft: typeof draftSealingPlan) =>
  c.tools.register(
    defineTool({
      name: flow.name,
      description,
      parameters,
      output: {
        schema: { type: 'json' as const },
        render: (_args: unknown, v: any) => [
          {
            type: 'text' as const,
            text: JSON.stringify({ summary: v.card.summary, warnings: v.card.warnings, brief_markdown: v.brief_markdown, next_step: v.next_step, record_ids: v.record_ids, result: v.result, human_input_request: v.human_input_request, delivered: v.delivered }),
          },
        ],
      },
      execute: async (raw: unknown, exec: any) => {
        const args = raw as { panel: string; human_inputs?: HumanInput[]; leak_m3_min?: number; target_o2_pct?: number }
        const parent = exec.agent
        if (!parent) throw new Error(`${flow.name} 需要调用方会话`)
        const workspace: string = await c.workingDirectory.ensure(parent, exec.signal)
        const session = String(parent.id)
        const runId = `${flow.name}-${randomUUID()}`
        append(parent.session, 'tool-workflow/run-start', { runId, name: `${args.panel} ${flow.label}` })
        let stopReason = 'completed'
        let n = 0
        try {
          const out: any = await draft(evidence, {
            panel: String(args.panel),
            humanInputs: args.human_inputs ?? [],
            ...(typeof args.leak_m3_min === 'number' ? { leakM3Min: args.leak_m3_min } : {}),
            ...(typeof args.target_o2_pct === 'number' ? { targetO2Pct: args.target_o2_pct } : {}),
            origin: { channel: 'web', trigger: 'chat', caller: null, conversation_id: null },
            signal: exec.signal,
            maxRounds: config.sealingMaxRounds,
            ...(config.disableStage ? {} : { stage: stageRunner(parent, runId) }),
            precheck: (plan) => precheck(exec, plan, ++n, flow.issueTool),
            getArticles: (document, articles) => knowledge.getArticles(document, articles).results,
          })
          const dir = join(workspace, 'records', session)
          await mkdir(dir, { recursive: true })
          const reportPath = join(dir, `${out.card.record_ids[0]}.md`)
          await writeFile(reportPath, `${out.report_markdown}\n`, 'utf8')
          const presented = await c.tools.execute({
            callId: `${exec.callId}:present`,
            rootCallId: exec.rootCallId,
            name: 'present',
            arguments: { files: [{ path: reportPath, description: `${args.panel} ${flow.label}与合规审核意见` }] },
            agent: parent,
            parent: exec.token,
            signal: exec.signal,
          })
          await logToIncident(String(args.panel), flow === FIREFIGHTING ? 'firefighting_plan' : 'sealing_plan', out)
          out.delivered = presented.isError
            ? { ok: false, error: presented.error?.message ?? '交付失败' }
            : { ok: true, files: [reportPath], note: '方案文件已交付给用户，不要再调用 present' }
          return toJson(out)
        } catch (err) {
          stopReason = 'failed'
          throw err
        } finally {
          append(parent.session, 'tool-workflow/run-end', { runId, stopReason })
        }
      },
    }),
  )

  registerPlanFlow(
    SEALING,
    '编制采空区火区封闭方案（固定流程，指挥部决定封闭时一律调它，不要自己拼步骤）：按固定步骤采集证据，由代码生成现场条件' +
    '（甲烷、氧浓度、爆炸危险性、封闭时间、人工确认项）和必备步骤，“方案编写”阶段选可选步骤和密闭巷道，代码计算密闭材料、注浆量、注氮流量并与库存比较，' +
    '经 actions 红线预检和“合规审核”阶段（只引用条文索引给出的条文），不通过时自动修改重审，轮数有上限。' +
    '完整方案和审核意见已作为文件交付（网页端右侧预览）；回答时把 brief_markdown（方案要点）原样单独成段放进回答，不要改写、不要贴完整方案、不要另写分析；next_step 是给你的下一步说明，不用贴。' +
    'result.verdict 为 pass 时，下发用 issue_sealing_plan，参数只给 record_id = result.issue_record_id（不要提交方案内容）；' +
    '不通过时照实转告，不要绕过。返回 human_input_request 时用 ask_user 逐项询问（写明确认人），带着 human_inputs 重新调用。' +
    '人工确认项（灾区温度、能见度、火源是否明确、水源是否充足、回风道是否畅通）只能由人提供，不要替人填写。方案结构和参数为开发占位值。',
    {
    panel: { type: 'string', required: true, description: '工作面编号，如 1201' },
    human_inputs: {
      type: 'array',
      description: '人工确认的现场条件（只填指挥人员明确给出的）：[{field, value, confirmed_by, note?}]，field 取 area_temperature_c、visibility_m、fire_source_confirmed、water_supply_sufficient、return_airway_clear',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          field: { type: 'string', required: true, enum: ['area_temperature_c', 'visibility_m', 'fire_source_confirmed', 'water_supply_sufficient', 'return_airway_clear'] },
          value: { type: 'json', required: true, description: '温度、能见度为数值；其余为 true / false' },
          confirmed_by: { type: 'string', required: true, description: '确认人（姓名或岗位）' },
          note: { type: 'string' },
        },
      },
    },
    leak_m3_min: { type: 'number', description: '采空区漏风量（m³/min），指挥人员给出时才填；与 target_o2_pct 一起用于由代码估算注氮流量' },
    target_o2_pct: { type: 'number', description: '注氮的目标氧浓度（%），指挥人员给出时才填' },
      },
    draftSealingPlan,
  )

  registerPlanFlow(
    FIREFIGHTING,
    '编制采空区防灭火处置方案（固定流程，不封闭火区、不安排人员进入灾区；预警达到橙色或指挥部要求注惰、堵漏、阻化、注浆等处置时调它，不要自己拼步骤）：' +
    '按固定步骤采集证据，由代码生成现场条件（甲烷、氧浓度、爆炸危险性）和必备步骤（检测观测、注惰），“方案编写”阶段选可选步骤（堵漏、阻化剂、均压、注浆、处置后取样），' +
    '代码计算注氮流量、注浆量并与库存比较，经 actions 红线预检和“合规审核”阶段（只引用条文索引给出的条文），不通过时自动修改重审，轮数有上限。' +
    '完整方案和审核意见已作为文件交付（网页端右侧预览）；回答时把 brief_markdown（方案要点）原样单独成段放进回答，不要改写、不要贴完整方案、不要另写分析；next_step 是给你的下一步说明，不用贴。' +
    'result.verdict 为 pass 时，下发用 issue_firefighting_plan，参数只给 record_id = result.issue_record_id（不要提交方案内容）；不通过时照实转告，不要绕过。' +
    '不需要向人要现场条件（没有人工确认项）。方案结构和参数为开发占位值。',
    {
      panel: { type: 'string', required: true, description: '工作面编号，如 1201' },
      leak_m3_min: { type: 'number', description: '采空区漏风量（m³/min），指挥人员给出时才填；与 target_o2_pct 一起用于由代码估算注氮流量' },
      target_o2_pct: { type: 'number', description: '注氮的目标氧浓度（%），指挥人员给出时才填' },
    },
    draftFirefightingPlan,
  )

  /** 把报告写成会话工作区里的 Markdown 文件，用 present 交付（网页端显示交付卡片、右侧预览） */
  async function deliver(exec: any, fileName: string, markdown: string, description: string) {
    const workspace: string = await c.workingDirectory.ensure(exec.agent, exec.signal)
    const dir = join(workspace, 'records', String(exec.agent.id))
    await mkdir(dir, { recursive: true })
    const path = join(dir, fileName)
    await writeFile(path, `${markdown}\n`, 'utf8')
    const r = await c.tools.execute({ callId: `${exec.callId}:present`, rootCallId: exec.rootCallId, name: 'present', arguments: { files: [{ path, description }] }, agent: exec.agent, parent: exec.token, signal: exec.signal })
    return r.isError ? { ok: false, error: r.error?.message ?? '交付失败' } : { ok: true, files: [path], note: '报告文件已交付给用户，不要再调用 present' }
  }

  /** 经 DSH 工具管道调用调用方 Agent 范围内的只读工具，取结构化结果 */
  async function callJson(exec: any, name: string, args: Record<string, unknown>, tag: string): Promise<any> {
    const r = await c.tools.execute({ callId: `${exec.callId}:${tag}`, rootCallId: exec.rootCallId, name, arguments: args, agent: exec.agent, parent: exec.token, signal: exec.signal })
    if (r.isError) throw new Error(r.error?.message ?? `${name} 调用失败`)
    if (r.value && typeof r.value === 'object' && !Array.isArray(r.value)) return r.value
    const text = (r.content ?? []).find((b: any) => b.type === 'text')?.text
    return text ? JSON.parse(text) : null
  }

  c.tools.register(
    defineTool({
      name: EFFECT_FLOW,
      description:
        '处置效果复评（固定流程，评估注氮、堵漏、封闭等处置的效果一律调它）：对工作面全部发火指标测点（采空区束管 CO、采空区测温、非进风侧安全监控 CO）' +
        '比较处置前后的上升速率，给出总体结论（已受控 / 需继续处置 / 恶化 / 观测时间不足）、各结论测点数、逐点结果和下次复评时刻，存为记录。' +
        'since 为处置开始的矿井时间（指挥人员给了就照填）；不给时取指令台账里本工作面最近一次已下发的封闭方案或防灭火方案的下发时间，台账里也没有时会返回说明，这时问指挥人员。' +
        '完整报告已作为文件交付（网页端右侧预览）；回答时把 brief_markdown（效果评估要点）原样单独成段放进回答，不要改写、不要贴完整报告、不要另写分析。判据为开发占位值。',
      parameters: {
        panel: { type: 'string', required: true, description: '工作面编号，如 1201' },
        since: { type: 'string', description: '处置开始的矿井时间，如 2026-11-04T00:00:00（不带时区）' },
      },
      output: {
        schema: { type: 'json' as const },
        render: (_args: unknown, v: any) => [
          { type: 'text' as const, text: JSON.stringify(v.error ? v : { summary: v.card.summary, warnings: v.card.warnings, brief_markdown: v.brief_markdown, record_ids: v.record_ids, result: v.result, delivered: v.delivered }) },
        ],
      },
      execute: async (raw: unknown, exec: any) => {
        const args = raw as { panel: string; since?: string }
        const parent = exec.agent
        if (!parent) throw new Error(`${EFFECT_FLOW} 需要调用方会话`)
        const runId = `${EFFECT_FLOW}-${randomUUID()}`
        append(parent.session, 'tool-workflow/run-start', { runId, name: `${args.panel} 处置效果复评` })
        let stopReason = 'completed'
        try {
          const out: any = await reviewEffect(evidence, {
            panel: String(args.panel),
            ...(args.since ? { since: String(args.since) } : {}),
            origin: { channel: 'web', trigger: 'chat', caller: null, conversation_id: null },
            signal: exec.signal,
            lastIssued: async (panel) => ((await callJson(exec, config.lastIssuedTool, { panel }, 'last-issued')) ?? { found: false }) as LastIssued,
          })
          await logToIncident(String(args.panel), 'effect_review', out)
          out.delivered = await deliver(exec, `${out.card.record_ids[0]}.md`, out.report_markdown, `${args.panel} 工作面处置效果复评报告`)
          return toJson(out)
        } catch (err) {
          stopReason = 'failed'
          if (err instanceof EffectInputError) return toJson({ error: err.message })
          throw err
        } finally {
          append(parent.session, 'tool-workflow/run-end', { runId, stopReason })
        }
      },
    }),
  )

  c.tools.register(
    defineTool({
      name: TIMELINE_FLOW,
      description:
        '事件复盘（固定流程，事件稳定或关闭后复盘、总结处置过程时调它）：读事件库里的事件，整理成按时间排序的时间线，' +
        '由代码计算告警、研判、复评、对外动作（已下发、被拒绝、执行失败）、跟踪计划的次数和发现异常到研判、到下发的响应时长，存为记录。' +
        'incident_id 不给就取最近更新的事件。完整复盘报告已作为文件交付（网页端右侧预览）；回答时把 brief_markdown（要点）原样单独成段放进回答，不要改写、不要贴完整报告、不要另写分析。' +
        '复盘只描述已发生的事，次数和时长照抄。',
      parameters: { incident_id: { type: 'string', description: '事件编号（INC-…）；缺省为最近更新的事件' } },
      output: {
        schema: { type: 'json' as const },
        render: (_args: unknown, v: any) => [
          { type: 'text' as const, text: JSON.stringify(v.error ? v : { summary: v.card.summary, warnings: v.card.warnings, brief_markdown: v.brief_markdown, record_ids: v.record_ids, result: v.result, delivered: v.delivered }) },
        ],
      },
      execute: async (raw: unknown, exec: any) => {
        const args = raw as { incident_id?: string }
        const parent = exec.agent
        if (!parent) throw new Error(`${TIMELINE_FLOW} 需要调用方会话`)
        if (!incidents) return toJson({ error: '没有配置事件库（fire-loop 的 incidentDir），不能复盘' })
        const runId = `${TIMELINE_FLOW}-${randomUUID()}`
        append(parent.session, 'tool-workflow/run-start', { runId, name: '事件复盘' })
        let stopReason = 'completed'
        try {
          const out: any = await reviewTimeline(evidence, {
            store: incidents,
            ...(args.incident_id ? { incidentId: String(args.incident_id) } : {}),
            origin: { channel: 'web', trigger: 'chat', caller: null, conversation_id: null },
            signal: exec.signal,
            receipt: (mineReceiptId) => (c.rescueEvidence as RescueEvidence).mineData.call('get_action_status', { action_id: mineReceiptId }, exec.signal),
          })
          out.delivered = await deliver(exec, `${out.card.record_ids[0]}.md`, out.report_markdown, `${out.result.incident_id} 事件复盘报告`)
          return toJson(out)
        } catch (err) {
          stopReason = 'failed'
          if (err instanceof TimelineInputError) return toJson({ error: err.message })
          throw err
        } finally {
          append(parent.session, 'tool-workflow/run-end', { runId, stopReason })
        }
      },
    }),
  )

  c.tools.register(
    defineTool({
      name: UNSEAL_FLOW,
      description:
        '启封评估（固定流程，问火区能不能启封、要求启封时一律先调它）：逐项判断封闭时长、氧浓度、CO 及上升趋势、乙烯、采空区温度是否满足启封条件（全部由代码计算），' +
        '具备条件时按固定步骤生成启封方案（先检查、断电、撤人，再启封），存为记录。完整报告已作为文件交付；回答时把 brief_markdown（要点）原样单独成段放进回答，不要改写、不要另写分析。' +
        'result.ready 为 true 且指挥人员要求启封时，调 approve_unsealing，record_id 照抄 result.issue_record_id，不要提交方案内容；ready 不是 true 时不要提交，照抄不满足的项。判据为开发占位值。',
      parameters: { panel: { type: 'string', required: true, description: '工作面编号，如 1201' } },
      output: {
        schema: { type: 'json' as const },
        render: (_args: unknown, v: any) => [
          { type: 'text' as const, text: JSON.stringify({ summary: v.card.summary, warnings: v.card.warnings, brief_markdown: v.brief_markdown, record_ids: v.record_ids, result: v.result, delivered: v.delivered }) },
        ],
      },
      execute: async (raw: unknown, exec: any) => {
        const args = raw as { panel: string }
        const parent = exec.agent
        if (!parent) throw new Error(`${UNSEAL_FLOW} 需要调用方会话`)
        const runId = `${UNSEAL_FLOW}-${randomUUID()}`
        append(parent.session, 'tool-workflow/run-start', { runId, name: `${args.panel} 启封评估` })
        let stopReason = 'completed'
        try {
          const out: any = await assessUnsealing(evidence, {
            panel: String(args.panel),
            origin: { channel: 'web', trigger: 'chat', caller: null, conversation_id: null },
            signal: exec.signal,
          })
          await logToIncident(String(args.panel), 'unsealing', out)
          out.delivered = await deliver(exec, `${out.card.record_ids[0]}.md`, out.report_markdown, `${args.panel} 工作面启封评估报告`)
          return toJson(out)
        } catch (err) {
          stopReason = 'failed'
          throw err
        } finally {
          append(parent.session, 'tool-workflow/run-end', { runId, stopReason })
        }
      },
    }),
  )
}
