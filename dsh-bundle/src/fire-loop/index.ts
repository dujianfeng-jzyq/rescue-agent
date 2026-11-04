/**
 * rescue-fire-loop：火灾闭环的固定流程插件（docs/design-v2.md 4.4，ADR 0007）。本阶段（P1）只有 run_fire_assessment。
 *
 * 进程内调用证据插件的服务 rescueEvidence（采集、运行技能脚本、渲染报告），所以必须和证据插件放在同一个
 * 带 isolate 的 cordis:group 里（profiles/v2.patch.yml）。步骤在 assessment.ts，判据全在技能脚本里。
 *
 * 阶段“研判说明”用 ctx.subagents.startActivation 起子 Agent（与调用方同一组合，delivery: caller，outputSchema），
 * 用 tools.guard 限制它只能调 structured_output（子 Agent 的编号先预留，守卫从第一次调用起就生效；
 * 委派出的子 Agent 审批策略为 never，即使守卫没拦住，actions 也会被自动拒绝，验证清单 #19）。
 * 报告写进会话工作区，用 present 交付（报告和风险图）。concludeTurn 默认不开：rc.4 网页端在工具结束本轮时
 * 只显示折叠的“Completed”，看不到报告和交付卡片（2026-11-03 截图核对），所以仍由主预设把报告原样放进回答。
 * 运行过程写 tool-workflow/* 事件，网页端在工具调用下显示运行节点（#19）。
 */

import { randomUUID } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

import type { Context } from '../adapter/dsh.ts'
import { defineTool, Schema } from '../adapter/dsh.ts'
import type RescueEvidence from '../evidence/index.ts'
import { FLOW, runFireAssessment } from './assessment.ts'
import { STAGE, type StageRunner } from './explanation.ts'

export const name = 'rescue-fire-loop'
export const inject = ['tools', 'rescueEvidence', 'workingDirectory', 'subagents']

export interface Config {
  /** 起阶段子 Agent 的后端（本机进程内） */
  stageProvider: string
  /** 阶段子 Agent 的时限（秒）；超时判阶段失败，报告只含事实部分 */
  stageTimeoutS: number
  /** 关掉阶段“研判说明”（报告只含事实部分） */
  disableStage: boolean
  /** 交付报告后结束本轮（concludeTurn）。rc.4 网页端此时看不到报告，默认关 */
  concludeTurn: boolean
}

export const Config: Schema<Config> = Schema.object({
  stageProvider: Schema.string().default('spawn'),
  stageTimeoutS: Schema.number().default(120),
  disableStage: Schema.boolean().default(false),
  concludeTurn: Schema.boolean().default(false),
})

const STRUCTURED_OUTPUT = 'structured_output'
const toJson = (v: unknown) => JSON.parse(JSON.stringify(v)) as never

export function apply(ctx: Context, config: Config) {
  const c = ctx as any
  const evidence = (c.rescueEvidence as RescueEvidence).evidence
  const stageChildren = new Set<string>()

  // 阶段子 Agent 只能提交结构化结果
  c.tools.guard((e: any) => {
    const ids = [e.agent?.id, e.agent?.session?.id].filter(Boolean).map(String)
    if (ids.some((i) => stageChildren.has(i)) && e.name !== STRUCTURED_OUTPUT) return `“${STAGE}”阶段只能用 ${STRUCTURED_OUTPUT} 提交结果，不能调用 ${e.name}`
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
    return async ({ label, prompt, schema, signal }) => {
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
        append(parent.session, 'tool-workflow/agent-start', { runId, seq: 1, label, phase: label, childId: String(activation.childId) })
        const res = await Promise.race([
          activation.result,
          new Promise<never>((_, reject) => sig.addEventListener('abort', () => reject(new Error(timeout.aborted ? `超过 ${config.stageTimeoutS} 秒` : '已取消')), { once: true })),
        ])
        const ok = res?.stopReason === 'completed' && res?.structured !== undefined
        append(parent.session, 'tool-workflow/agent-end', { runId, seq: 1, outcome: ok ? 'completed' : 'failed' })
        return ok ? { ok: true, structured: res.structured, childId } : { ok: false, error: `子 Agent 没有给出结构化结果（${res?.stopReason ?? '未知'}）`, childId }
      } catch (err) {
        append(parent.session, 'tool-workflow/agent-end', { runId, seq: 1, outcome: 'failed' })
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
        '报告文件和风险图已由本工具交付（不要再调用 present）；回答时把 report_markdown（事实部分 + 研判说明 + 风险图）原样单独成段放进回答，研判说明已给出关注点和下一步，不要再另写分析与建议，回答到此结束。' +
        '返回 card（结果卡片）、report_markdown、record_ids、result；发布预警、升级响应时引用 record_ids 里的记录编号。阈值和判据为开发占位值。',
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
            text: JSON.stringify({ summary: v.card.summary, warnings: v.card.warnings, report_markdown: v.report_markdown, record_ids: v.record_ids, result: v.result, delivered: v.delivered }),
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
}
