import assert from 'node:assert/strict'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import { Evidence } from '../src/evidence/service.ts'
import { assessUnsealing } from '../src/fire-loop/unsealing.ts'

const PYTHON = process.env.RESCUE_TEST_PYTHON ?? new URL('../../mcp/.venv/bin/python', import.meta.url).pathname
const SKILLS = new URL('../../skills', import.meta.url).pathname
const CASES = new URL('../../skills/unsealing-plan/tests/cases/unsealing_plan/', import.meta.url)
const ORIGIN = { channel: 'web', trigger: 'chat', caller: null, conversation_id: null } as const

// 假矿井：直接返回技能样例里的证据内容（采集清单只用到这几个工具；这里按样例的数据回答）
async function fakeMine(caseName: string) {
  const ev = JSON.parse(await readFile(new URL(`${caseName}.input.json`, CASES), 'utf8')).evidence
  const sensors = Object.values<any>(ev.groups).flatMap((g) => g.sensors)
  const readings = Object.values<any>(ev.groups).flatMap((g) => g.readings)
  const latest = Object.values<any>(ev.groups).flatMap((g) => g.latest)
  return async (tool: string, args: any): Promise<any> => {
    switch (tool) {
      case 'get_clock':
        return { now: ev.as_of }
      case 'list_sensors':
        return { result: [...new Map(sensors.map((s) => [s.id, s])).values()] }
      case 'get_latest':
        return { result: latest.filter((r) => args.sensor_ids.includes(r.sensor_id)) }
      case 'get_readings':
        return { result: readings.filter((r) => args.sensor_ids.includes(r.sensor_id) && args.measurands.includes(r.measurand)) }
      case 'get_fire_areas':
        return { result: ev.fire_areas }
      case 'get_network':
        return ev.network
      default:
        throw new Error(`没有 ${tool}`)
    }
  }
}

async function run(caseName: string) {
  const root = await mkdtemp(join(tmpdir(), 'unseal-'))
  const ev = new Evidence({ skillsDir: SKILLS, recordDir: join(root, 'records'), runDir: join(root, 'runs'), python: PYTHON, mineData: await fakeMine(caseName) })
  return assessUnsealing(ev, { panel: '1201', origin: ORIGIN })
}

describe('assess_unsealing', () => {
  it('具备条件：生成启封方案，要点里给下发说明，issue_record_id 是启封评估记录', async () => {
    const out = await run('s5-336h-ready')
    assert.equal(out.result.ready, true)
    assert.equal(out.result.step_count, 5)
    assert.equal(out.result.issue_record_id, out.record_ids.unseal)
    assert.match(out.brief_markdown, /^【启封评估要点】1201 工作面/)
    assert.match(out.brief_markdown, /具备启封条件/)
    assert.match(out.report_markdown, /1\. 启封前检查/)
    assert.equal(out.card.next_actions[0]?.tool, 'approve_unsealing')
  })

  it('不具备条件：没有方案、没有下发入口，逐项说明', async () => {
    const out = await run('s5-240h-sealed-too-short')
    assert.equal(out.result.ready, false)
    assert.equal(out.result.issue_record_id, null)
    assert.equal(out.result.unmet_labels, '封闭时长')
    assert.match(out.report_markdown, /不具备启封条件的项：封闭时长/)
    assert.doesNotMatch(out.report_markdown, /启封方案（共/)
    assert.deepEqual(out.card.next_actions, [])
  })
})
