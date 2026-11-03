/**
 * rescue-evidence：证据插件（docs/design-v2.md 4.3，ADR 0007）。
 *
 * 提供 cordis 服务 `rescueEvidence`（固定流程插件在进程内调用：采集、运行技能脚本、读记录），
 * 并给模型注册只读工具 get_record、export_record、check_mine（一次性风险检测；持续检测在 P4）。review_timeline 在 P4 加。
 *
 * - 自带 MCP 客户端直连 mine_data（只读令牌），不经模型的工具范围（验证清单 #19、#20）；
 * - 技能脚本在 DSH 进程起的子进程里运行，不在 bash 沙箱里，环境变量按白名单传（#22）；
 * - 记录目录只有本插件写；bash 沙箱写不了，actions 只读。
 */

import type { Context } from '../adapter/dsh.ts'
import { defineTool, Schema, Service } from '../adapter/dsh.ts'
import { checkMine, defaultPanels } from './check.ts'
import { McpClient } from './mcp-client.ts'
import { Evidence } from './service.ts'

export interface Config {
  mineDataUrl: string
  /** mine_data 只读令牌（不是 actions 的令牌，也不是 /sim 控制令牌） */
  mineDataToken?: string
  mineDataTimeoutMs: number
  skillsDir: string
  recordDir: string
  python: string
}

const jsonOutput = {
  schema: { type: 'json' as const },
  render: (_args: unknown, value: unknown) => [{ type: 'text' as const, text: JSON.stringify(value, null, 2) }],
}

/** 记录都是从 JSON 解析来的，类型上转成工具要求的 JsonValue */
const toJson = (v: unknown) => v as Parameters<typeof JSON.stringify>[0] as never

declare module '@deepseek-ai/cordis' {
  interface Context {
    rescueEvidence: RescueEvidence
  }
}

export default class RescueEvidence extends Service {
  static inject = ['tools', 'workingDirectory']

  static Config: Schema<Config> = Schema.object({
    mineDataUrl: Schema.string().default('http://127.0.0.1:8101/mcp'),
    mineDataToken: Schema.string(),
    mineDataTimeoutMs: Schema.number().default(30_000),
    skillsDir: Schema.string().required(),
    recordDir: Schema.string().required(),
    python: Schema.string().default('python3'),
  })

  readonly evidence: Evidence
  readonly mineData: McpClient

  constructor(ctx: Context, config: Config) {
    super(ctx, 'rescueEvidence')
    this.mineData = new McpClient({ server: 'mine_data', url: config.mineDataUrl, token: config.mineDataToken, timeoutMs: config.mineDataTimeoutMs })
    this.evidence = new Evidence({
      skillsDir: config.skillsDir,
      recordDir: config.recordDir,
      python: config.python,
      mineData: (tool, args) => this.mineData.call(tool, args),
    })
    const c = ctx as any

    c.tools.register(
      defineTool({
        name: 'get_record',
        description: '按编号读取一条记录（证据 EV-…、结果 RS-…、报告 RP-…）。记录不可修改；指令引用记录时按编号核对。',
        parameters: { record_id: { type: 'string', required: true, description: '记录编号' } },
        output: jsonOutput,
        execute: async (args: { record_id: string }) => toJson(await this.evidence.getRecord(String(args.record_id))),
      }),
    )

    c.tools.register(
      defineTool({
        name: 'check_mine',
        description:
          '矿井风险检测（几秒）：按 fire-assessment 技能的标准逐个工作面检测必需测点、实时红线报警、预警等级和自燃阶段，' +
          '与正常状态比较给出是否异常（anomaly）及原因，每个工作面存为检测记录。回答“现在矿井是否有异常”时调它，照抄 card 和 report_markdown。' +
          'mode 只支持 once（一次性检测）；持续检测以后提供。panels 缺省为技能配置的全部工作面。',
        parameters: {
          mode: { type: 'string', required: true, enum: ['once', 'watch'], description: 'once：一次性检测' },
          panels: { type: 'array', items: { type: 'string' }, description: '工作面编号；缺省为全部' },
        },
        output: jsonOutput,
        execute: async (args: { mode: string; panels?: string[] }, exec: any) => {
          if (args.mode !== 'once') throw new Error('持续检测（mode=watch）还没有提供，请用 mode=once 做一次性检测')
          const panels = args.panels?.length ? args.panels.map(String) : await defaultPanels(config.skillsDir)
          const out = await checkMine(this.evidence, {
            panels,
            origin: { channel: 'web', trigger: 'chat', caller: null, conversation_id: null },
            signal: exec.signal,
          })
          return toJson(out)
        },
      }),
    )

    c.tools.register(
      defineTool({
        name: 'export_record',
        description:
          '把一条记录（及其附带文件）写进会话工作区，返回路径，供按技能在沙箱里做进一步分析。沙箱里算出的结果只作参考，不能被指令引用。',
        parameters: { record_id: { type: 'string', required: true, description: '记录编号' } },
        output: jsonOutput,
        execute: async (args: { record_id: string }, exec: any) => {
          if (!exec.agent) throw new Error('export_record 需要调用方会话')
          const workspace: string = await c.workingDirectory.ensure(exec.agent, exec.signal)
          return toJson({ record_id: args.record_id, ...(await this.evidence.exportRecord(String(args.record_id), workspace, String(exec.agent.id))) })
        },
      }),
    )
  }
}
