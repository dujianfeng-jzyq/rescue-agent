import assert from 'node:assert/strict'
import { mkdtemp, readFile, readdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import { buildRecord, mineTimeToSeconds, newRecordId, RecordNotFound, RecordStore, RecordTampered, verify } from '../src/evidence/records.ts'

const FIXTURE = new URL('../../mcp/contract/tests/fixtures/record-from-ts.json', import.meta.url)

const sample = () =>
  buildRecord({
    kind: 'result',
    panel: '1201',
    asOf: '2026-11-07T08:00:00.5',
    origin: { channel: 'web', trigger: 'chat', caller: '调度员' },
    source: { skill: 'fire-assessment', skill_version: '0.1.0', script: 'warning', script_sha256: 'a'.repeat(64), input_sha256: 'b'.repeat(64) },
    content: { algorithm_version: 'w-1', params: { co_yellow_ppm: 24 }, warnings: [], result: { level: 'yellow', co_ppm: 46.7, ratio: 1e-7 } },
    files: [{ name: 'chart.svg', media_type: 'image/svg+xml', size: 6, sha256: 'c'.repeat(64) }],
    parentIds: ['EV-20261107T080000-abc123'],
    now: new Date('2026-11-03T14:00:00.000Z'),
    recordId: 'RS-20261107T080000-x1y2z3',
  })

describe('记录', () => {
  it('与 Python 侧共用的样例记录一致（Python 测试读同一文件）', async () => {
    assert.deepEqual(sample(), JSON.parse(await readFile(FIXTURE, 'utf8')))
  })

  it('编号格式与矿井时间', () => {
    assert.match(newRecordId('evidence', '2026-11-07T08:00'), /^EV-20261107T080000-[a-z0-9]{6}$/)
    assert.equal(mineTimeToSeconds('2026-11-07T08:00:00.123'), '2026-11-07T08:00:00')
    assert.throws(() => newRecordId('result', '2026-11-07T08:00:00+08:00'))
  })

  it('核对：前缀、哈希', () => {
    assert.throws(() => verify({ ...sample(), kind: 'report' }), RecordTampered)
    assert.throws(() => verify({ ...sample(), content: { result: 'changed' } }), RecordTampered)
  })

  it('存储：读回、不覆盖、篡改可发现、编号不合法', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'records-'))
    const store = new RecordStore(dir)
    const rec = await store.put(sample())
    assert.deepEqual(await store.get(rec.record_id), rec)
    await assert.rejects(store.put(rec), /不能覆盖/)
    assert.deepEqual((await readdir(dir)).filter((f) => f.endsWith('.tmp')), [])
    const path = join(dir, `${rec.record_id}.json`)
    await writeFile(path, (await readFile(path, 'utf8')).replace('46.7', '16.7'))
    await assert.rejects(store.get(rec.record_id), RecordTampered)
    await assert.rejects(store.get('../etc/passwd'), RecordNotFound)
    await assert.rejects(store.get('RS-20261107T080000-zzzzzz'), RecordNotFound)
  })
})
