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
}

/** clock 返回矿井时间；返回写入的事件编号，没有事件返回 undefined */
export async function noteOnIncident(store: IncidentStore | undefined, panel: string, note: TimelineNote, clock: () => Promise<string>): Promise<string | undefined> {
  if (!store) return undefined
  const found = await store.findOpen('fire', panel)
  if (!found) return undefined
  await store.append(found.incident_id, await clock(), 'note', 'flow', { ...note })
  return found.incident_id
}
