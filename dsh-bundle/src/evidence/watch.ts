/**
 * 持续检测的核心（design-v2 4.9，不依赖 DSH，单独测试）：基线检测 → 每个检测周期（矿井时间）检测一次 → 发现异常、
 * 到期或取数连续失败时结束。判据全在 fire-assessment 的 check.py（与基线比较、与上一次比较判数据中断）；
 * 这里只按 watch.yaml 的编排参数（interval_min、max_hours、fetch_failures）安排周期。
 *
 * 插件入口（index.ts）把它登记成 DSH 后台任务：按真实时间轮询矿井时钟，到周期就调 tick()；
 * 一致性和期望测试（tools/watch.ts）直接驱动它，每推进一个周期调一次 tick()。
 */

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

import { parse as parseYaml } from 'yaml'

import { CHECK_SKILL } from './check.ts'
import type { Origin } from './records.ts'
import type { Evidence } from './service.ts'

export interface WatchParams {
  interval_min: number
  max_hours: number
  fetch_failures: number
}

export async function watchParams(skillsDir: string): Promise<WatchParams> {
  const w = parseYaml(await readFile(join(skillsDir, CHECK_SKILL, 'references', 'watch.yaml'), 'utf8'))
  for (const k of ['interval_min', 'max_hours', 'fetch_failures'] as const) {
    if (typeof w?.[k] !== 'number' || w[k] <= 0) throw new Error(`${CHECK_SKILL}/references/watch.yaml 缺少 ${k}`)
  }
  return { interval_min: w.interval_min, max_hours: w.max_hours, fetch_failures: w.fetch_failures }
}

export interface PanelState {
  panel: string
  baseline_id: string
  previous_id: string
  last: CheckSummary
}

export interface CheckSummary {
  as_of: string
  anomaly: boolean
  reasons: string[]
  level: string
  level_name: string
  stage: string
  stage_name: string
  alarm_count: number
  evidence_id: string
  result_id: string
}

export type WatchEnd =
  | { reason: 'anomaly'; anomaly: true }
  | { reason: 'expired'; anomaly: false }
  | { reason: 'data-unavailable'; anomaly: true; detail: string }

export interface WatchResult {
  anomaly: boolean
  end_reason: WatchEnd['reason']
  summary: string
  started_at: string
  ended_at: string
  cycles: number
  panels: (CheckSummary & { panel: string; baseline_id: string; report_id: string | null; report_markdown: string | null })[]
  report_markdown: string
  record_ids: string[]
}

const mineMs = (t: string) => Date.parse(`${t.slice(0, 19)}Z`)

export class Watcher {
  readonly panels: PanelState[] = []
  cycles = 0
  startedAt = ''
  lastAt = ''
  private failures = 0
  private lastError = ''
  private readonly ev: Evidence
  private readonly opts: { panels: string[]; origin: Origin; params: WatchParams; signal?: AbortSignal }

  constructor(ev: Evidence, opts: { panels: string[]; origin: Origin; params: WatchParams; signal?: AbortSignal }) {
    this.ev = ev
    this.opts = opts
  }

  private async check(panel: string, inputs: Record<string, string>): Promise<CheckSummary> {
    const sig = this.opts.signal ? { signal: this.opts.signal } : {}
    const evidence = await this.ev.collectEvidence({ skill: CHECK_SKILL, kind: 'check', panel, origin: this.opts.origin })
    const { record } = await this.ev.runScript({ skill: CHECK_SKILL, script: 'check', inputs: { evidence: evidence.record_id, ...inputs }, origin: this.opts.origin, ...sig })
    const r = (record.content as any).result
    return {
      as_of: record.as_of,
      anomaly: Boolean(r.anomaly),
      reasons: r.reasons,
      level: r.level,
      level_name: r.level_name,
      stage: r.stage,
      stage_name: r.stage_name,
      alarm_count: r.alarm_count,
      evidence_id: evidence.record_id,
      result_id: record.record_id,
    }
  }

  /** 基线检测（与正常状态比较）。返回已有实时报警的工作面：启动时就有报警的不进入等待，直接按异常报告（4.9） */
  async start(): Promise<CheckSummary[]> {
    for (const panel of this.opts.panels) {
      const s = await this.check(panel, {})
      this.panels.push({ panel, baseline_id: s.result_id, previous_id: s.result_id, last: s })
    }
    this.startedAt = this.lastAt = this.panels[0]?.last.as_of ?? ''
    return this.panels.filter((p) => p.last.alarm_count > 0).map((p) => p.last)
  }

  /** 到了检测周期没有：矿井时间距上一次检测不少于 interval_min */
  due(mineNow: string): boolean {
    return mineMs(mineNow) - mineMs(this.lastAt) >= this.opts.params.interval_min * 60_000
  }

  /** 一个检测周期：逐个工作面检测；返回结束原因，或 null（继续） */
  async tick(): Promise<WatchEnd | null> {
    try {
      for (const p of this.panels) {
        const s = await this.check(p.panel, { baseline: p.baseline_id, previous: p.previous_id })
        p.previous_id = s.result_id
        p.last = s
      }
      this.failures = 0
    } catch (err) {
      if (this.opts.signal?.aborted) throw err
      this.failures += 1
      this.lastError = (err as Error).message
      if (this.failures >= this.opts.params.fetch_failures) return { reason: 'data-unavailable', anomaly: true, detail: `取数连续失败 ${this.failures} 次：${this.lastError}` }
      return null
    }
    this.cycles += 1
    this.lastAt = this.panels[0]!.last.as_of
    if (this.panels.some((p) => p.last.anomaly)) return { reason: 'anomaly', anomaly: true }
    if (mineMs(this.lastAt) - mineMs(this.startedAt) >= this.opts.params.max_hours * 3600_000) return { reason: 'expired', anomaly: false }
    return null
  }

  get failureCount(): number {
    return this.failures
  }

  /** 结束时的结果：异常的工作面渲染检测报告（检测结果的文字），作为后台任务的结果交给被唤醒的智能体 */
  async finish(end: WatchEnd): Promise<WatchResult> {
    const panels = []
    for (const p of this.panels) {
      let report: { record_id: string; content: unknown } | null = null
      if (p.last.anomaly) report = await this.ev.renderReport({ skill: CHECK_SKILL, template: 'check', inputs: { check: p.last.result_id }, origin: this.opts.origin })
      panels.push({ panel: p.panel, baseline_id: p.baseline_id, ...p.last, report_id: report?.record_id ?? null, report_markdown: report ? (report.content as string).trimEnd() : null })
    }
    const hours = Math.round(((mineMs(this.lastAt) - mineMs(this.startedAt)) / 3600_000) * 10) / 10
    const summary =
      end.reason === 'anomaly'
        ? `发现异常：${panels.filter((p) => p.anomaly).map((p) => `${p.panel}（${p.reasons.join('；')}）`).join('；')}`
        : end.reason === 'expired'
          ? `检测到期（${this.opts.params.max_hours} 矿井小时）、未见异常`
          : `数据中断：${end.detail}`
    return {
      anomaly: end.anomaly,
      end_reason: end.reason,
      summary: `${summary}。持续检测 ${this.startedAt} 至 ${this.lastAt}（${hours} 矿井小时），共 ${this.cycles} 个检测周期`,
      started_at: this.startedAt,
      ended_at: this.lastAt,
      cycles: this.cycles,
      panels,
      report_markdown: panels.filter((p) => p.report_markdown).map((p) => p.report_markdown).join('\n\n'),
      record_ids: panels.flatMap((p) => [p.result_id, ...(p.report_id ? [p.report_id] : [])]),
    }
  }
}
