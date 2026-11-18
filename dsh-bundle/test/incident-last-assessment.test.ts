import assert from 'node:assert/strict'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import { IncidentStore } from '../src/incident/store.ts'
import { lastAssessmentNote, noteOnIncident } from '../src/incident/timeline.ts'

describe('lastAssessmentNote：复评找不到下发时的观察起点', () => {
  it('取该工作面未关闭事件里最近一次研判的数据时间和记录编号；复评等其他条目不算', async () => {
    const store = new IncidentStore(await mkdtemp(join(tmpdir(), 'inc-')))
    assert.equal(await lastAssessmentNote(store, '211016'), undefined, '没有事件')
    assert.equal(await lastAssessmentNote(undefined, '211016'), undefined, '没有配置事件库')
    await store.open('2026-11-02T14:43:00', { category: 'fire', panel: '211016', summary: 's', by: 'sentinel' })
    assert.equal(await lastAssessmentNote(store, '211016'), undefined, '还没有研判')
    const clock = async () => '2026-11-02T17:00:00'
    await noteOnIncident(store, '211016', { type: 'assessment', text: 'a1', record_ids: ['RP-1'], as_of: '2026-11-02T14:43:38' }, clock)
    await noteOnIncident(store, '211016', { type: 'assessment', text: 'a2', record_ids: ['RP-2', 'RS-x'], as_of: '2026-11-02T16:39:41', level: 'none' }, clock)
    await noteOnIncident(store, '211016', { type: 'effect_review', text: 'e', record_ids: ['RP-3'], as_of: '2026-11-02T18:00:00' }, clock)
    assert.deepEqual(await lastAssessmentNote(store, '211016'), { as_of: '2026-11-02T16:39:41', record_id: 'RP-2', level: 'none' })
    assert.equal(await lastAssessmentNote(store, '1202'), undefined, '别的工作面')
  })
})
