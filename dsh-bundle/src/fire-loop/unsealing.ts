/**
 * 固定流程 assess_unsealing（第三版遗留项：启封审批按记录重做）：启封评估。步骤由代码决定，判据和方案步骤全在 unsealing-plan 技能里，没有模型阶段。
 *
 *   采集证据（unsealing-plan 的 unseal 清单）→ unsealing_plan.py（逐项判定，具备条件时生成方案）→ 报告和要点
 *
 * 结论为“具备启封条件”时，下发用 guard 的 approve_unsealing（只提交 record_ids.unseal）；不具备时没有方案，也不能提交。
 */

import { randomInt } from 'node:crypto'

import type { Origin } from '../evidence/records.ts'
import type { Evidence } from '../evidence/service.ts'
import type { FlowEvent } from './assessment.ts'

export const UNSEAL_FLOW = 'assess_unsealing'
const UP = 'unsealing-plan'

export interface UnsealingOptions {
  panel: string
  origin: Origin
  signal?: AbortSignal
}

export async function assessUnsealing(ev: Evidence, opts: UnsealingOptions) {
  const runId = `UA-${Date.now().toString(36)}-${randomInt(36 ** 4).toString(36).padStart(4, '0')}`
  const events: FlowEvent[] = []
  const emit = (stage: string, status: FlowEvent['status'], recordIds: string[] = [], detail: string | null = null) =>
    events.push({ flow: UNSEAL_FLOW, run_id: runId, stage, status, at: new Date().toISOString(), record_ids: recordIds, detail })
  const origin = opts.origin
  const ids: Record<string, string> = {}
  const step = async <T extends { record_id: string }>(stage: string, fn: () => Promise<T>): Promise<T> => {
    emit(stage, 'started')
    try {
      const r = await fn()
      ids[stage] = r.record_id
      emit(stage, 'completed', [r.record_id])
      return r
    } catch (err) {
      emit(stage, 'failed', [], (err as Error).message)
      throw err
    }
  }
  const evidence = await step('evidence', () => ev.collectEvidence({ skill: UP, kind: 'unseal', panel: opts.panel, origin }))
  const unseal = await step('unseal', () =>
    ev.runScript({ skill: UP, script: 'unsealing_plan', inputs: { evidence: evidence.record_id }, origin, ...(opts.signal ? { signal: opts.signal } : {}) }).then((x) => x.record),
  )
  const report = await step('report', () => ev.renderReport({ skill: UP, template: 'report', inputs: { unseal: unseal.record_id }, origin }))
  const brief = await step('brief', () => ev.renderReport({ skill: UP, template: 'brief', inputs: { unseal: unseal.record_id }, origin }))
  const r = (unseal.content as any).result
  return {
    card: {
      title: `${opts.panel} 工作面启封评估`,
      summary: `${r.ready_name}；共 ${r.total_count} 项判定：${r.met_count} 项满足，${r.unmet_count} 项不满足${r.ready ? '' : `（不满足：${r.unmet_labels}）`}`,
      record_ids: [report.record_id, unseal.record_id],
      files: [],
      next_actions: r.ready ? [{ label: '批准启封（approve_unsealing，record_id 用启封评估记录）', tool: 'approve_unsealing', arguments: { record_id: unseal.record_id } }] : [],
      warnings: ['判据和方案步骤为开发占位值，待专家审定', ...((unseal.content as any).warnings ?? [])],
    },
    report_markdown: (report.content as string).trimEnd(),
    brief_markdown: (brief.content as string).trimEnd(),
    record_ids: ids,
    result: {
      panel: opts.panel,
      as_of: r.as_of,
      ready: r.ready,
      ready_name: r.ready_name,
      total_count: r.total_count,
      met_count: r.met_count,
      unmet_count: r.unmet_count,
      unmet_labels: r.unmet_labels,
      step_count: r.step_count,
      issue_record_id: r.ready ? unseal.record_id : null,
    },
    events,
  }
}
