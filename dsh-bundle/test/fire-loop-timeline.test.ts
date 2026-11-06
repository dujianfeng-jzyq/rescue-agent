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
  it('对外动作的矿井回执：逐个向矿井查后续状态，查不到的记原因', async () => {
    const { store, ev } = await setup()
    const id = await store.open('2026-11-06T08:00:00', { category: 'fire', panel: '1201', summary: 'x' })
    await store.append(id, '2026-11-06T08:10:00', 'note', 'guard', { type: 'action', tool: 'publish_warning', status: 'issued', action_id: 'A-1', record_ids: [], problems: [], mine_receipt_ids: ['M-1', 'M-2'] })
    const asked: string[] = []
    const receipt = async (mid: string) => {
      asked.push(mid)
      if (mid === 'M-2') throw new Error('矿井接口不通')
      return { action_id: mid, kind: 'notice', status: 'completed', received_at: '2026-11-06T08:10:00', updated_at: '2026-11-06T08:10:00', idempotency_key: 'k', detail: null }
    }
    const out = await reviewTimeline(ev, { store, incidentId: id, origin: ORIGIN, receipt })
    assert.deepEqual(asked, ['M-1', 'M-2'])
    assert.equal(out.result.receipts_text, '矿井回执 2 条：已完成 1、执行中 0、失败或被矿井拒绝 0、查不到 1')
    assert.match(out.report_markdown, /publish_warning A-1 → 矿井回执 M-1：通知已完成/)
    assert.match(out.report_markdown, /矿井回执 M-2：查不到（矿井接口不通）/)
    // 没有对外动作、或没给 receipt：没有回执部分
    const none = await reviewTimeline(ev, { store, incidentId: 'INC-20261105T080000-aaaaaa', origin: ORIGIN, receipt })
    assert.equal(none.result.receipts_text, null)
  })

  it('回执并行查（有并发上限），每个回执只查一次，结果与串行一致', async () => {
    const { store, ev } = await setup()
    const id = await store.open('2026-11-06T08:00:00', { category: 'fire', panel: '1201', summary: 'x' })
    const mids = Array.from({ length: 20 }, (_, i) => `M-${i + 1}`)
    await store.append(id, '2026-11-06T08:10:00', 'note', 'guard', { type: 'action', tool: 'issue_sealing_plan', status: 'issued', action_id: 'A-1', record_ids: [], problems: [], mine_receipt_ids: mids })
    await store.append(id, '2026-11-06T08:20:00', 'note', 'guard', { type: 'action', tool: 'publish_warning', status: 'issued', action_id: 'A-2', record_ids: [], problems: [], mine_receipt_ids: ['M-1', 'M-2'] }) // 重复的回执编号
    const asked: string[] = []
    let running = 0
    let peak = 0
    const receipt = async (mid: string) => {
      asked.push(mid)
      running++
      peak = Math.max(peak, running)
      await new Promise((r) => setTimeout(r, 10))
      running--
      return { action_id: mid, kind: 'treatment', status: 'completed', received_at: '2026-11-06T08:10:00', updated_at: '2026-11-06T08:10:00', idempotency_key: 'k', detail: null }
    }
    const out = await reviewTimeline(ev, { store, incidentId: id, origin: ORIGIN, receipt })
    assert.deepEqual([...asked].sort(), [...mids].sort()) // 每个只查一次
    assert.ok(peak > 1 && peak <= 6, `并发峰值 ${peak}`)
    // 脚本按各次对外动作引用的回执计数（M-1、M-2 被两次动作引用，算 22 条），查询只发一次
    assert.equal(out.result.receipts_text, '矿井回执 22 条：已完成 22、执行中 0、失败或被矿井拒绝 0、查不到 0')
  })

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
