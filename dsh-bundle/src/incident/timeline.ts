/**
 * 把固定流程的结果自动记进事件时间线（第三版 P4）：由代码写，不靠模型记得。
 * 该工作面有未关闭的火情事件才记；没有事件（一次性提问）时什么都不做。事件库的写入失败不影响流程结果。
 */

import { IncidentStore } from './store.ts'

export interface TimelineNote {
  /** assessment / effect_review / sealing_plan */
  type: string
  text: string
  record_ids: string[]
  /** 依据的数据时间（引用记录里最早的 as_of，矿井时间）；告警是否已处理按它判断（见 alertStatuses） */
  as_of?: string
  /** 复评（effect_review）给出的下次复评时刻（矿井时间）：add_followup 不给 after_h 且之后没有新处置时按它到点 */
  next_review_at?: string
  /** 研判（assessment）的预警等级：add_followup 按它限间隔、无预警且没下发过处置时不登记跟踪 */
  level?: string
}

/**
 * 引用记录里最早的取数时间（矿井时间）。取最早的：只要有一条依据比告警旧，就不能说这次处理覆盖了告警。
 * 读不到的记录跳过；一条都读不到返回 undefined。
 */
export async function evidenceAsOf(recordIds: string[], read: (id: string) => Promise<{ as_of?: unknown } | null | undefined>): Promise<string | undefined> {
  let min: string | undefined
  for (const id of recordIds) {
    try {
      const v = (await read(id))?.as_of
      if (typeof v !== 'string' || !v) continue
      const t = v.slice(0, 19)
      if (min === undefined || t < min) min = t
    } catch {
      // 读不到的记录不影响写时间线
    }
  }
  return min
}

/**
 * 该工作面未关闭火情事件里最近一次研判（固定流程写进时间线的 assessment 条目）的依据数据时间和记录编号。
 * 复评找不到已下发的处置时，用它作观察起点（2026-11-18：无预警时跟踪到点，复评不该去问操作员从什么时候算）。
 * 没有事件、没有研判或研判没记 as_of 时返回 undefined。
 */
export async function lastAssessmentNote(store: IncidentStore | undefined, panel: string): Promise<{ as_of: string; record_id: string | null; level: string | null } | undefined> {
  if (!store) return undefined
  const found = await store.findOpen('fire', panel)
  if (!found) return undefined
  const entries = await store.entries(found.incident_id)
  for (let i = entries.length - 1; i >= 0; i--) {
    const d = entries[i]!.data as Record<string, any> | undefined
    if (entries[i]!.kind === 'note' && d?.type === 'assessment' && typeof d.as_of === 'string' && d.as_of) {
      const ids = Array.isArray(d.record_ids) ? d.record_ids : []
      return { as_of: d.as_of.slice(0, 19), record_id: typeof ids[0] === 'string' ? ids[0] : null, level: typeof d.level === 'string' ? d.level : null }
    }
  }
  return undefined
}

/** clock 返回矿井时间；返回写入的事件编号，没有事件返回 undefined */
export async function noteOnIncident(store: IncidentStore | undefined, panel: string, note: TimelineNote, clock: () => Promise<string>): Promise<string | undefined> {
  if (!store) return undefined
  const found = await store.findOpen('fire', panel)
  if (!found) return undefined
  const { as_of, ...rest } = note
  await store.append(found.incident_id, await clock(), 'note', 'flow', { ...rest, ...(as_of ? { as_of } : {}) })
  return found.incident_id
}
