import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import { Evidence, ScriptFailed } from '../src/evidence/service.ts'

const PYTHON = process.env.RESCUE_TEST_PYTHON ?? new URL('../../mcp/.venv/bin/python', import.meta.url).pathname
const ORIGIN = { channel: 'web', trigger: 'chat', caller: '调度员' } as const

// 假 mine_data：两个束管测点、一个上隅角测点
const SENSORS = [
  { id: 'BT-1201-G2', name: '束管 2', source: 'bundle_tube', measurands: ['CO', 'O2', 'CO2'], roadway_id: 'R1', panel: '1201', role: 'goaf_gas_tube' },
  { id: 'BT-1201-G1', name: '束管 1', source: 'bundle_tube', measurands: ['CO', 'O2', 'CO2'], roadway_id: 'R1', panel: '1201', role: 'goaf_gas_tube' },
  { id: 'BT-1202-G1', name: '别的工作面的束管', source: 'bundle_tube', measurands: ['CO', 'O2', 'CO2'], roadway_id: 'R1', panel: '1202', role: 'goaf_gas_tube' },
  { id: 'SM-1201-UC', name: '上隅角', source: 'safety_monitoring', measurands: ['CO', 'CH4'], roadway_id: 'R2', panel: '1201', role: 'upper_corner' },
]
const reading = (sensor_id: string, measurand: string, value: number) => ({ sensor_id, measurand, ts: '2026-11-07T08:00:00', value, quality: 'ok' })
async function mineData(tool: string, args: any): Promise<any> {
  switch (tool) {
    case 'get_clock':
      return { now: '2026-11-07T08:00:00.75' }
    case 'list_sensors':
      return { result: SENSORS }
    case 'get_latest':
      return { result: [reading('BT-1201-G2', 'CO', 46.7), reading('BT-1201-G1', 'CO', 12), reading('BT-1201-G1', 'O2', 15.2), reading('SM-1201-UC', 'CH4', 0.4)].filter((r) => args.sensor_ids.includes(r.sensor_id)) }
    default:
      throw new Error(`没有 ${tool}`)
  }
}

const SCRIPT = `
import argparse, json
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--input-file"); ap.add_argument("--output-file"); ap.add_argument("--svg-out")
a = ap.parse_args()
inp = json.loads(Path(a.input_file).read_text(encoding="utf-8"))
if inp.get("options", {}).get("fail"):
    raise SystemExit("故意失败")
co = [r["value"] for r in inp["evidence"]["groups"]["tubes"]["latest"] if r["measurand"] == "CO"]
Path(a.svg_out).write_text("<svg>max</svg>", encoding="utf-8")
Path(a.output_file).write_text(json.dumps({"algorithm_version": "m-1", "params": {}, "warnings": [], "result": {"max_co_ppm": max(co)}}), encoding="utf-8")
`

async function setup() {
  const root = await mkdtemp(join(tmpdir(), 'evidence-'))
  const skill = join(root, 'skills', 'demo-check')
  await mkdir(join(skill, 'scripts'), { recursive: true })
  await mkdir(join(skill, 'references'))
  await writeFile(join(skill, 'SKILL.md'), '---\nname: demo-check\ndescription: 测试\nwhenToUse: 测试时\nversion: 0.2.0\n---\n')
  await writeFile(join(skill, 'skill.yaml'), 'scripts:\n  max_co:\n    file: scripts/max_co.py\n    svg: true\n')
  await writeFile(join(skill, 'scripts', 'max_co.py'), SCRIPT)
  await writeFile(
    join(skill, 'references', 'collect.yaml'),
    'status: placeholder\nkinds:\n  check:\n    sensors:\n      - name: tubes\n        select: {role: goaf_gas_tube, panel: true}\n        measurands: [CO]\n        required: true\n',
  )
  const recordDir = join(root, 'records')
  return { root, recordDir, ev: new Evidence({ skillsDir: join(root, 'skills'), recordDir, python: PYTHON, mineData }) }
}

describe('Evidence（证据插件核心）', () => {
  it('采集 → 运行脚本 → 结果记录 → 导出；Python 侧能读并核对', async () => {
    const { root, recordDir, ev } = await setup()
    const evidence = await ev.collectEvidence({ skill: 'demo-check', kind: 'check', panel: '1201', origin: ORIGIN })
    assert.match(evidence.record_id, /^EV-20261107T080000-/)
    assert.equal(evidence.as_of, '2026-11-07T08:00:00')
    const content = evidence.content as any
    assert.deepEqual(content.groups.tubes.sensors.map((s: any) => s.id), ['BT-1201-G1', 'BT-1201-G2'])
    assert.deepEqual(content.groups.tubes.latest.map((r: any) => `${r.sensor_id}/${r.measurand}`), ['BT-1201-G1/CO', 'BT-1201-G2/CO'])
    assert.equal((evidence.source as any).skill_version, '0.2.0')

    const { record } = await ev.runScript({ skill: 'demo-check', script: 'max_co', inputs: { evidence: evidence.record_id }, origin: ORIGIN })
    assert.match(record.record_id, /^RS-20261107T080000-/)
    assert.deepEqual((record.content as any).result, { max_co_ppm: 46.7 })
    assert.deepEqual(record.parent_ids, [evidence.record_id])
    assert.equal((record.source as any).script_file, 'scripts/max_co.py')
    assert.equal((await ev.readRecordFile(record.record_id, 'chart.svg')).toString(), '<svg>max</svg>')

    const out = await ev.exportRecord(record.record_id, join(root, 'workspace'), 'session-abc')
    assert.equal(out.path, join(root, 'workspace', 'records', 'session-abc', `${record.record_id}.json`))
    assert.equal(JSON.parse(await readFile(out.path, 'utf8')).record_id, record.record_id)
    assert.equal(out.files.length, 1)
    await assert.rejects(ev.exportRecord(record.record_id, join(root, 'workspace'), '../x'), /会话编号不合法/)

    // Python 侧（actions 将来这样核对）读 TS 写的两条记录
    const py = `
import sys
from rescue_contract.records import RecordStore
s = RecordStore(sys.argv[1])
for rid in sys.argv[2:]:
    r = s.get(rid)
    print(r.kind.value, r.content_sha256[:12])
`
    const printed = execFileSync(PYTHON, ['-c', py, recordDir, evidence.record_id, record.record_id], { encoding: 'utf8' })
    assert.deepEqual(printed.trim().split('\n').map((l) => l.split(' ')[0]), ['evidence', 'result'])
  })

  it('脚本失败、脚本未登记、输入记录不存在', async () => {
    const { ev } = await setup()
    const evidence = await ev.collectEvidence({ skill: 'demo-check', kind: 'check', panel: '1201', origin: ORIGIN })
    await assert.rejects(
      ev.runScript({ skill: 'demo-check', script: 'max_co', inputs: { evidence: evidence.record_id }, options: { fail: true }, origin: ORIGIN }),
      (err: unknown) => err instanceof ScriptFailed && err.run.status === 'exit' && /故意失败/.test(err.message),
    )
    await assert.rejects(ev.runScript({ skill: 'demo-check', script: 'nope', inputs: { evidence: evidence.record_id }, origin: ORIGIN }), /没有登记脚本/)
    await assert.rejects(ev.runScript({ skill: 'demo-check', script: 'max_co', inputs: { evidence: 'EV-20261107T080000-zzzzzz' }, origin: ORIGIN }), /找不到记录/)
    await assert.rejects(ev.collectEvidence({ skill: 'demo-check', kind: 'nope', panel: '1201', origin: ORIGIN }), /采集清单没有 nope/)
  })
})
