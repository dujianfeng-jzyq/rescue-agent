import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import type { RescueRecord } from '../src/evidence/records.ts'
import { previousWarningRecord } from '../src/fire-loop/assessment.ts'

type Entry = { kind: string; by?: string; data?: Record<string, any> }

const store = (entries: Entry[] | null) => ({
  findOpen: async (_c: string, _p: string) => (entries ? { incident_id: 'INC-1' } : undefined),
  entries: async (_id: string) => entries ?? [],
})
const rec = (id: string, panel: string, skill: string, script: string) => ({ record_id: id, panel, source: { skill, script } }) as unknown as RescueRecord
const records: Record<string, RescueRecord> = {
  'RS-w1': rec('RS-w1', '211016', 'fire-assessment', 'warning'),
  'RS-w2': rec('RS-w2', '211016', 'fire-assessment', 'warning'),
  'RS-stage': rec('RS-stage', '211016', 'fire-assessment', 'stage'),
  'RS-other': rec('RS-other', '1201', 'fire-assessment', 'warning'),
}
const get = async (id: string) => {
  const r = records[id]
  if (!r) throw new Error(`没有记录 ${id}`)
  return r
}
const assessment = (warning_record_id?: string): Entry => ({ kind: 'note', by: 'flow', data: { type: 'assessment', level: 'red', ...(warning_record_id ? { warning_record_id } : {}) } })

describe('定级回差的“上一次”（previousWarningRecord）', () => {
  it('取本事件最近一次研判记下的预警等级记录', async () => {
    const s = store([assessment('RS-w1'), { kind: 'note', by: 'flow', data: { type: 'effect_review' } }, assessment('RS-w2')])
    assert.equal(await previousWarningRecord(s, '211016', get), 'RS-w2')
  })

  it('没有事件、没记过（旧时间线）、不是预警等级记录、别的工作面、读不到：不给，按阈值定级', async () => {
    assert.equal(await previousWarningRecord(store(null), '211016', get), undefined)
    assert.equal(await previousWarningRecord(store([assessment()]), '211016', get), undefined)
    assert.equal(await previousWarningRecord(store([assessment('RS-stage')]), '211016', get), undefined)
    assert.equal(await previousWarningRecord(store([assessment('RS-other')]), '211016', get), undefined)
    assert.equal(await previousWarningRecord(store([assessment('RS-missing')]), '211016', get), undefined)
  })
})
