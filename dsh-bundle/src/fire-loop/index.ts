/**
 * rescue-fire-loop：火灾闭环的固定流程插件（docs/design-v2.md 4.4，ADR 0007）。本阶段（P1）只有 run_fire_assessment。
 *
 * 进程内调用证据插件的服务 rescueEvidence（采集、运行技能脚本、渲染报告），所以必须和证据插件放在同一个
 * 带 isolate 的 cordis:group 里（profiles/v2.patch.yml）。步骤在 assessment.ts，判据全在技能脚本里。
 */

import type { Context } from '../adapter/dsh.ts'
import { defineTool, Schema } from '../adapter/dsh.ts'
import type RescueEvidence from '../evidence/index.ts'
import { FLOW, runFireAssessment } from './assessment.ts'

export const name = 'rescue-fire-loop'
export const inject = ['tools', 'rescueEvidence', 'workingDirectory']

export interface Config {}
export const Config: Schema<Config> = Schema.object({})

const toJson = (v: unknown) => v as Parameters<typeof JSON.stringify>[0] as never

export function apply(ctx: Context, _config: Config) {
  const c = ctx as any
  const evidence = (c.rescueEvidence as RescueEvidence).evidence

  c.tools.register(
    defineTool({
      name: FLOW,
      description:
        '工作面采空区自燃的完整研判（固定流程，研判、要不要升级、有没有爆炸危险一律先调它）：按固定步骤采集证据，' +
        '计算实时红线报警、预警等级、自燃发展阶段、触发指标的趋势、全部束管测点的爆炸三角形（取最不利，附风险图）、' +
        '受威胁人员（等级达到阈值或 include_personnel=true 时）和升级建议，每步存为记录。' +
        '返回 card（结果卡片）、report_markdown（报告事实部分，含风险图和升级建议）、record_ids、result。' +
        '回答时把 report_markdown 原样单独成段放进回答，再写“分析与建议”；发布预警、升级响应时引用 record_ids 里的记录编号。' +
        '阈值和判据为开发占位值。',
      parameters: {
        panel: { type: 'string', required: true, description: '工作面编号，如 1201' },
        include_personnel: { type: 'boolean', description: '等级未达到阈值时也计算受威胁人员' },
      },
      output: {
        schema: { type: 'json' as const },
        // 模型看到的：卡片、报告、记录编号和结论摘要；流程事件只留在结果值里
        render: (_args: unknown, v: any) => [
          { type: 'text' as const, text: JSON.stringify({ card: v.card, report_markdown: v.report_markdown, record_ids: v.record_ids, result: v.result }, null, 1) },
        ],
      },
      execute: async (args: { panel: string; include_personnel?: boolean }, exec: any) => {
        if (!exec.agent) throw new Error(`${FLOW} 需要调用方会话`)
        const workspace: string = await c.workingDirectory.ensure(exec.agent, exec.signal)
        const session = String(exec.agent.id)
        const out = await runFireAssessment(evidence, {
          panel: String(args.panel),
          includePersonnel: Boolean(args.include_personnel),
          origin: { channel: 'web', trigger: 'chat', caller: null, conversation_id: null },
          signal: exec.signal,
          // 风险图导出到会话工作区（records/<会话>/），网页端经 /api/file 显示
          chartPath: async (id) => (await evidence.exportRecord(id, workspace, session)).files[0] ?? null,
        })
        return toJson(out)
      },
    }),
  )
}
