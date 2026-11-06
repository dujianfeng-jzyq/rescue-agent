import assert from 'node:assert/strict'
import { copyFile, mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import { Evidence } from '../src/evidence/service.ts'
import { IncidentStore } from '../src/incident/store.ts'
import { reviewTimeline, TimelineInputError } from '../src/fire-loop/timeline.ts'

const PYTHON = process.env.RESCUE_TEST_PYTHON ?? new URL('../../mcp/.venv/bin/python', import.meta.url).pathname
const SKILLS = new URL('../../skills', import.meta.url).pathname
const FIX = new URL('../../mcp/contract/tests/fixtures/incidents/', import.meta.url)
const ORIGIN = { channel: 'web', trigger: 'chat', caller: null, conversation_id: null } as const

async function setup() {
  const root = await mkdtemp(join(tmpdir(), 'timeline-'))
  const dir = join(root, 'incidents')
  const store = new IncidentStore(dir)
  await import('node:fs/promises').then((fs) => fs.mkdir(dir, { recursive: true }))
  await copyFile(new URL('INC-20261105T080000-aaaaaa.jsonl', FIX), join(dir, 'INC-20261105T080000-aaaaaa.jsonl'))
  const ev = new Evidence({ skillsDir: SKILLS, recordDir: join(root, 'records'), runDir: join(root, 'runs'), python: PYTHON, mineData: async () => ({}) })
  return { store, ev }
}

describe('review_timeline', () => {
  it('读事件、存证据、脚本算次数和时长、渲染报告和要点', async () => {
    const { store, ev } = await setup()
    const out = await reviewTimeline(ev, { store, incidentId: 'INC-20261105T080000-aaaaaa', origin: ORIGIN })
    assert.equal(out.result.incident_id, 'INC-20261105T080000-aaaaaa')
    assert.equal(out.result.counts.alerts, 4)
    assert.ok(out.result.unhandled_alert_count >= 1)
    assert.match(out.brief_markdown, /^【事件复盘要点】INC-20261105T080000-aaaaaa/)
    assert.match(out.report_markdown, /时间线：/)
    assert.match(out.report_markdown, /登记跟踪计划/)
    assert.deepEqual(Object.keys(out.record_ids), ['evidence', 'timeline', 'report', 'brief'])
    // 证据记录能按编号读回，内容就是事件库的条目（不可修改、可核对）
    const rec: any = await ev.getRecord(out.record_ids.evidence!)
    assert.equal(rec.content.entries.length, 12)
    assert.equal(rec.source.from, 'incident-store:INC-20261105T080000-aaaaaa')
  })

  it('不给编号取最近更新的事件；没有事件、编号不存在时给说明', async () => {
    const { store, ev } = await setup()
    const out = await reviewTimeline(ev, { store, origin: ORIGIN })
    assert.equal(out.result.incident_id, 'INC-20261105T080000-aaaaaa')
    await assert.rejects(reviewTimeline(ev, { store, incidentId: 'INC-20261105T080000-zzzzzz', origin: ORIGIN }), TimelineInputError)
    const empty = new IncidentStore(join(await mkdtemp(join(tmpdir(), 'empty-')), 'x'))
    await assert.rejects(reviewTimeline(ev, { store: empty, origin: ORIGIN }), /还没有事件/)
  })
})
