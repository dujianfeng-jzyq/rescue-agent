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

/** clock 返回矿井时间；返回写入的事件编号，没有事件返回 undefined */
export async function noteOnIncident(store: IncidentStore | undefined, panel: string, note: TimelineNote, clock: () => Promise<string>): Promise<string | undefined> {
  if (!store) return undefined
  const found = await store.findOpen('fire', panel)
  if (!found) return undefined
  const { as_of, ...rest } = note
  await store.append(found.incident_id, await clock(), 'note', 'flow', { ...rest, ...(as_of ? { as_of } : {}) })
  return found.incident_id
}
