/**
 * 风险检测（design-v2 4.9）：按 fire-assessment 技能的采集清单（check）取证据 → check.py → 检测结果的文字（templates/check.md）。
 * 判据全在技能里（check.py、references/watch.yaml），这里只做编排。一次性检测（once）在 P1；持续检测（watch）在 P4。
 */

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'

import { parse as parseYaml } from 'yaml'

import type { Origin } from './records.ts'
import type { Evidence } from './service.ts'

export const CHECK_SKILL = 'fire-assessment'

export interface PanelCheck {
  panel: string
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
  report_id: string
  report_markdown: string
}

export interface CheckOutcome {
  card: {
    title: string
    summary: string
    record_ids: string[]
    files: never[]
    next_actions: { label: string; tool: string | null; arguments: Record<string, unknown> }[]
    warnings: string[]
  }
  anomaly: boolean
  panels: PanelCheck[]
  report_markdown: string
}

/**
 * check_mine 缺省检测的工作面：技能 references/watch.yaml 的 panels；环境变量 RESCUE_WATCH_PANELS（逗号分隔）覆盖，与监听器一致。
 * 原来只读 watch.yaml：演示换了工作面名（DEMO_PANEL=211016）时 check_mine 还在查 1201，报“缺必需测点”，智能体去研判一个不存在的工作面。
 */
export async function defaultPanels(skillsDir: string, env: Record<string, string | undefined> = process.env): Promise<string[]> {
  const fromEnv = (env.RESCUE_WATCH_PANELS ?? '').split(',').map((p) => p.trim()).filter(Boolean)
  if (fromEnv.length) return fromEnv
  const watch = parseYaml(await readFile(join(skillsDir, CHECK_SKILL, 'references', 'watch.yaml'), 'utf8'))
  const panels = watch?.panels
  if (!Array.isArray(panels) || !panels.length) throw new Error(`${CHECK_SKILL}/references/watch.yaml 没有 panels`)
  return panels.map(String)
}

export async function checkPanel(ev: Evidence, panel: string, origin: Origin, signal?: AbortSignal): Promise<PanelCheck> {
  const evidence = await ev.collectEvidence({ skill: CHECK_SKILL, kind: 'check', panel, origin })
  const { record } = await ev.runScript({ skill: CHECK_SKILL, script: 'check', inputs: { evidence: evidence.record_id }, origin, ...(signal ? { signal } : {}) })
  const report = await ev.renderReport({ skill: CHECK_SKILL, template: 'check', inputs: { check: record.record_id }, origin })
  const r = (record.content as any).result
  return {
    panel,
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
    report_id: report.record_id,
    report_markdown: report.content as string,
  }
}

export async function checkMine(ev: Evidence, opts: { panels: string[]; origin: Origin; signal?: AbortSignal }): Promise<CheckOutcome> {
  const panels: PanelCheck[] = []
  for (const p of opts.panels) panels.push(await checkPanel(ev, p, opts.origin, opts.signal))
  const anomaly = panels.some((p) => p.anomaly)
  const flagged = panels.filter((p) => p.anomaly)
  return {
    card: {
      title: '风险检测',
      summary: panels.map((p) => `${p.panel}：${p.anomaly ? `发现异常（${p.reasons.join('；')}）` : '未见异常'}`).join('\n'),
      record_ids: panels.flatMap((p) => [p.result_id, p.report_id]),
      files: [],
      next_actions: flagged.map((p) => ({ label: `对 ${p.panel} 做完整研判`, tool: 'run_fire_assessment', arguments: { panel: p.panel } })),
      warnings: ['检测判据为开发初稿（占位值），待专家评审'],
    },
    anomaly,
    panels,
    report_markdown: panels.map((p) => p.report_markdown.trimEnd()).join('\n\n'),
  }
}
