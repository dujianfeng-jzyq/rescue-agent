/**
 * rescue-evidence：证据插件（docs/design-v2.md 4.3，ADR 0007）。
 *
 * 提供 cordis 服务 `rescueEvidence`（固定流程插件在进程内调用：采集、运行技能脚本、读记录），
 * 并给模型注册只读工具 get_record、export_record、check_mine（once：一次性检测；watch：持续检测，登记为 DSH 后台任务）。
 *
 * 持续检测（design-v2 4.9，验证清单 #20、#23）：基线检测后用 ctx.jobs.start 登记后台任务，任务在 DSH 进程里按真实时间
 * 轮询矿井时钟，到检测周期就检测一次（不经模型、不花 token），发现异常、到期或取数连续失败时结束，由 tool-jobs 唤醒智能体。
 * 被唤醒的这一轮以 error 结束时（provider 重试之后仍失败），按退避（占位 1、5、15 分钟）用 agent.followup 补发通知。
 *
 * - 自带 MCP 客户端直连 mine_data（只读令牌），不经模型的工具范围（验证清单 #19、#20）；
 * - 技能脚本在 DSH 进程起的子进程里运行，不在 bash 沙箱里，环境变量按白名单传（#22）；
 * - 记录目录只有本插件写；bash 沙箱写不了，actions 只读。
 */

import type { Context } from '../adapter/dsh.ts'
import { defineTool, Schema, Service } from '../adapter/dsh.ts'
import { randomUUID } from 'node:crypto'

import { checkMine, defaultPanels } from './check.ts'
import { McpClient } from './mcp-client.ts'
import { Evidence } from './service.ts'
import { Watcher, watchParams, type WatchResult } from './watch.ts'

export interface Config {
  mineDataUrl: string
  /** mine_data 只读令牌（不是 actions 的令牌，也不是 /sim 控制令牌） */
  mineDataToken?: string
  mineDataTimeoutMs: number
  skillsDir: string
  recordDir: string
  /** 技能脚本的运行目录（输入、输出、图）；缺省为记录目录旁的 runs/ */
  runDir?: string
  python: string
  /** 持续检测读矿井时钟的真实间隔（毫秒） */
  watchPollMs: number
  /** 持续检测的真实时间硬上限（小时） */
  watchMaxRealHours: number
  /** 唤醒失败后补发通知的退避（秒）；空表不补发 */
  redeliverBackoffS: number[]
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
  static inject = ['tools', 'workingDirectory', 'jobs']

  static Config: Schema<Config> = Schema.object({
    mineDataUrl: Schema.string().default('http://127.0.0.1:8101/mcp'),
    mineDataToken: Schema.string(),
    mineDataTimeoutMs: Schema.number().default(30_000),
    skillsDir: Schema.string().required(),
    recordDir: Schema.string().required(),
    runDir: Schema.string(),
    python: Schema.string().default('python3'),
    watchPollMs: Schema.number().default(2000),
    watchMaxRealHours: Schema.number().default(24),
    redeliverBackoffS: Schema.array(Schema.number()).default([60, 300, 900]),
  })

  readonly evidence: Evidence
  readonly mineData: McpClient

  constructor(ctx: Context, config: Config) {
    super(ctx, 'rescueEvidence')
    this.mineData = new McpClient({ server: 'mine_data', url: config.mineDataUrl, token: config.mineDataToken, timeoutMs: config.mineDataTimeoutMs })
    this.evidence = new Evidence({
      skillsDir: config.skillsDir,
      recordDir: config.recordDir,
      ...(config.runDir ? { runDir: config.runDir } : {}),
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

    // ── 持续检测：每个会话同时只能有一个；被唤醒的这一轮失败时补发通知 ──
    const active = new Map<string, string>() // owner → jobId
    type Pending = { jobId: string; notice: string; attempts: number; started: boolean; errored: boolean; timer?: ReturnType<typeof setTimeout> }
    const pending = new Map<string, Pending>()
    const notice = (text: string, summary: string) => ({
      id: randomUUID(),
      role: 'user',
      content: [{ type: 'text', text }],
      source: { kind: 'tool-jobs', form: 'notice', summary },
    })
    ctx.on('agent/error' as any, ({ agent }: any) => {
      const p = pending.get(String(agent?.id))
      if (p) p.errored = true
    })
    ctx.on('agent/status' as any, ({ agent, status }: any) => {
      const owner = String(agent?.id)
      const p = pending.get(owner)
      if (!p || p.timer) return
      if (status === 'running') {
        p.started = true
        return
      }
      if (status !== 'idle' || !p.started) return
      if (!p.errored) {
        pending.delete(owner)
        return
      }
      const backoff = config.redeliverBackoffS
      if (p.attempts >= backoff.length) {
        pending.delete(owner)
        ctx.logger.warn(`check_mine: 持续检测 ${p.jobId} 的唤醒失败，补发 ${p.attempts} 次后放弃（唤醒失败）`)
        return
      }
      const delay = backoff[p.attempts]! * 1000
      p.attempts += 1
      ctx.logger.warn(`check_mine: 持续检测 ${p.jobId} 唤醒的这一轮出错，${delay / 1000} 秒后第 ${p.attempts} 次补发通知`)
      p.timer = setTimeout(() => {
        p.timer = undefined
        p.started = false
        p.errored = false
        try {
          agent.followup(notice(`${p.notice}\n（上一轮处理这条通知时出错，系统第 ${p.attempts} 次补发。）`, `补发：后台任务 ${p.jobId} 已结束`))
        } catch (err) {
          pending.delete(owner)
          ctx.logger.warn(`check_mine: 补发通知失败：${(err as Error).message}`)
        }
      }, delay)
    })

    const startWatch = async (panels: string[], exec: any) => {
      const owner = exec.agent?.id ? String(exec.agent.id) : undefined
      if (!owner) throw new Error('check_mine(watch) 需要调用方会话')
      const running = active.get(owner)
      if (running) return { mode: 'watch', started: false, job_id: running, note: `本会话已有持续检测任务 ${running} 在运行；要重新开始，先用 job_kill 停掉它` }
      const params = await watchParams(config.skillsDir)
      const abort = new AbortController()
      const origin = { channel: 'web' as const, trigger: 'watch' as const, caller: null as string | null, conversation_id: null }
      const w = new Watcher(this.evidence, { panels, origin, params, signal: abort.signal })
      const alarmed = await w.start()
      if (alarmed.length) {
        // 启动时就有实时报警：不进入等待，直接按异常报告（design-v2 4.9）
        const res = await w.finish({ reason: 'anomaly', anomaly: true })
        return { mode: 'watch', started: false, note: '启动时已有实时红线报警，没有进入持续检测，直接按异常报告', result: res }
      }
      const label = `持续检测 ${panels.join('、')}（基线 ${w.panels.map((p) => `${p.panel} ${p.last.level_name}/${p.last.stage_name}`).join('；')}）`
      const realStart = Date.now()
      let jobId = ''
      jobId = c.jobs.start({
        kind: 'rescue-watch',
        label,
        owner,
        toolCall: { callId: exec.callId, rootCallId: exec.rootCallId },
        outputLimitBytes: 16_384,
        run: (job: any) => {
          origin.caller = jobId
          const done = (async () => {
            job.updateProgress(`基线 ${w.startedAt}；每 ${params.interval_min} 矿井分钟检测一次`)
            let end: Awaited<ReturnType<Watcher['tick']>> = null
            while (!abort.signal.aborted && !end) {
              if (Date.now() - realStart > config.watchMaxRealHours * 3600_000) break
              try {
                const now = String((await this.mineData.call('get_clock', {})).now)
                if (w.due(now)) {
                  end = await w.tick()
                  const p = w.panels.map((x) => `${x.panel} ${x.last.level_name}/${x.last.stage_name}`).join('；')
                  job.updateProgress(`最近检测 ${w.lastAt}：${p}；共 ${w.cycles} 个周期${w.failureCount ? `；取数失败 ${w.failureCount} 次` : ''}`)
                }
              } catch (err) {
                if (abort.signal.aborted) break
                job.append(`读矿井时钟失败：${(err as Error).message}\n`, { channel: 'stderr' })
              }
              if (!end) await new Promise((r) => setTimeout(r, config.watchPollMs))
            }
            if (abort.signal.aborted) return { status: 'killed' as const }
            const res: WatchResult = end ? await w.finish(end) : { ...(await w.finish({ reason: 'expired', anomaly: false })), summary: `达到真实时间上限（${config.watchMaxRealHours} 小时），结束持续检测、未见异常` }
            pending.set(owner, { jobId, notice: `后台任务 ${jobId}（持续检测）已结束：${res.anomaly ? '发现异常' : '未见异常'}。请用 job_output 读取结果并按规则处理。`, attempts: 0, started: false, errored: false })
            return { status: 'completed' as const, detail: res.end_reason, result: JSON.stringify(res) }
          })()
            .catch((err) => ({ status: 'failed' as const, detail: (err as Error).message }))
            .finally(() => active.delete(owner))
          return { cancel: () => abort.abort(), done }
        },
      })
      active.set(owner, jobId)
      return {
        mode: 'watch',
        started: true,
        job_id: jobId,
        baseline: w.panels.map((p) => ({ panel: p.panel, as_of: p.last.as_of, level_name: p.last.level_name, stage_name: p.last.stage_name, record_id: p.baseline_id })),
        interval_min: params.interval_min,
        max_hours: params.max_hours,
        note: '已在后台开始持续检测；告诉指挥人员后结束本轮，不要等待或轮询。发现异常、到期或数据中断时系统会通知你',
      }
    }

    c.tools.register(
      defineTool({
        name: 'check_mine',
        description:
          '矿井风险检测：按 fire-assessment 技能的标准逐个工作面检测必需测点、实时红线报警、预警等级和自燃阶段，每次存为检测记录。' +
          'mode=once：一次性检测（几秒），与正常状态比较给出是否异常及原因；回答“现在矿井是否有异常”时用，照抄 card 和 report_markdown。' +
          'mode=watch：持续检测，只在指挥人员明确要求时用。先做基线检测，再登记为后台任务并立即返回任务编号（告诉指挥人员已开始，结束本轮，不要轮询）；' +
          '后台按矿井时间每个检测周期检测一次，与基线比较，发现异常、到期或数据中断时任务结束，系统通知你，用 job_output 读结果。' +
          '一个会话同时只有一个持续检测。panels 缺省为技能配置的全部工作面。判据为开发初稿（占位值）。',
        parameters: {
          mode: { type: 'string', required: true, enum: ['once', 'watch'], description: 'once：一次性检测；watch：持续检测（后台任务）' },
          panels: { type: 'array', items: { type: 'string' }, description: '工作面编号；缺省为全部' },
        },
        output: jsonOutput,
        execute: async (args: { mode: string; panels?: string[] }, exec: any) => {
          const panels = args.panels?.length ? args.panels.map(String) : await defaultPanels(config.skillsDir)
          if (args.mode === 'watch') return toJson(await startWatch(panels, exec))
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
