import assert from 'node:assert/strict'
import { copyFile, mkdtemp, readFile, readdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import { IncidentNotFound, IncidentStore } from '../src/incident/store.ts'

const FIX = new URL('../../mcp/contract/tests/fixtures/incidents/', import.meta.url)

describe('事件库', () => {
  it('折叠结果与 Python 侧共用的样例一致', async () => {
    const store = new IncidentStore(FIX.pathname)
    const want = JSON.parse(await readFile(new URL('expected.json', FIX), 'utf8'))
    const got: Record<string, unknown> = {}
    for (const id of await store.ids()) got[id] = await store.get(id)
    assert.deepEqual(got, want)
  })

  it('开事件、追加、合并、关闭', async () => {
    const store = new IncidentStore(await mkdtemp(join(tmpdir(), 'inc-')))
    const id = await store.open('2026-11-05T08:00:00', { category: 'fire', panel: '1201', summary: 'x' })
    assert.match(id, /^INC-20261105T080000-[a-z0-9]{6}$/)
    assert.equal((await store.findOpen('fire', '1201'))?.incident_id, id)
    assert.equal(await store.findOpen('fire', '1202'), undefined)
    const a = await store.append(id, '2026-11-05T08:10:00', 'alert', 'sentinel', { summary: 's' })
    assert.deepEqual((await store.get(id)).pending_alerts, [a.event_id])
    await store.append(id, '2026-11-05T08:11:00', 'delivered', 'plugin', { alert_id: a.event_id })
    assert.deepEqual((await store.get(id)).pending_alerts, [])
    await store.append(id, '2026-11-05T09:00:00', 'status', 'agent', { status: 'closed' })
    assert.deepEqual(await store.list(), [])
    assert.equal((await store.list(true)).length, 1)
  })

  it('残行被跳过；不合法的编号和不存在的事件', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'inc-'))
    const name = 'INC-20261105T090000-bbbbbb.jsonl'
    await copyFile(new URL(name, FIX), join(dir, name))
    const store = new IncidentStore(dir)
    assert.equal((await store.entries('INC-20261105T090000-bbbbbb')).length, 4)
    await assert.rejects(store.get('../etc/passwd'), IncidentNotFound)
    await assert.rejects(store.append('INC-20261105T080000-zzzzzz', '2026-11-05T08:00:00', 'note', 'agent'), IncidentNotFound)
    await assert.rejects(store.get('INC-20261105T080000-zzzzzz'), IncidentNotFound)
    assert.deepEqual((await readdir(dir)).sort(), [name])
  })

  it('Python 和 TS 写出的行格式相同（字段、顺序）', async () => {
    const store = new IncidentStore(await mkdtemp(join(tmpdir(), 'inc-')))
    const id = await store.open('2026-11-05T08:00:00', { category: 'fire', panel: null, summary: 'x' })
    const line = JSON.parse((await readFile(join(store.dir, `${id}.jsonl`), 'utf8')).trim())
    const py = JSON.parse((await readFile(new URL('INC-20261105T080000-aaaaaa.jsonl', FIX), 'utf8')).split('\n')[0]!)
    assert.deepEqual(Object.keys(line), Object.keys(py))
    await writeFile(join(store.dir, 'ignored.txt'), '')
    assert.deepEqual(await store.ids(), [id])
  })
})
