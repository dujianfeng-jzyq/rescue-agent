import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readdir, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import { loadSkill, runScript, SkillError } from '../src/evidence/skills.ts'

// 与 mcp/contract/tests/test_skills.py 的临时技能同构：两边的运行规则要一致
const SCRIPT = `
import argparse, json, os, sys, time
from pathlib import Path
import yaml
from _lib import double
ap = argparse.ArgumentParser()
ap.add_argument("--input-file"); ap.add_argument("--output-file"); ap.add_argument("--svg-out")
a = ap.parse_args()
inp = json.loads(Path(a.input_file).read_text(encoding="utf-8"))
params = yaml.safe_load((Path(__file__).parent.parent / "references" / "params.yaml").read_text(encoding="utf-8"))
mode = inp.get("mode", "ok")
if mode == "sleep": time.sleep(30)
if mode == "fail":
    print("缺少测点 BT-1201-G1", file=sys.stderr); sys.exit(3)
if mode == "badjson":
    Path(a.output_file).write_text("不是 JSON", encoding="utf-8"); sys.exit(0)
if mode == "nokeys":
    Path(a.output_file).write_text(json.dumps({"result": 1}), encoding="utf-8"); sys.exit(0)
out = {"algorithm_version": "t-1", "params": {"k": params["k"]}, "warnings": [],
       "result": {"value": double(inp["x"]) * params["k"], "env": sorted(k for k in os.environ if "KEY" in k or "TOKEN" in k or k.startswith("PYTHON"))}}
if a.svg_out and mode != "nosvg": Path(a.svg_out).write_text("<svg/>", encoding="utf-8")
Path(a.output_file).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
`
const PYTHON = process.env.RESCUE_TEST_PYTHON ?? new URL('../../mcp/.venv/bin/python', import.meta.url).pathname

async function makeSkill(manifest?: string, fm?: string): Promise<{ root: string; dir: string }> {
  const root = await mkdtemp(join(tmpdir(), 'skills-'))
  const dir = join(root, 'demo-skill')
  await mkdir(join(dir, 'scripts'), { recursive: true })
  await mkdir(join(dir, 'references'))
  await writeFile(join(dir, 'SKILL.md'), `---\n${fm ?? 'name: demo-skill\ndescription: 测试\nwhenToUse: 测试时\nversion: 0.1.0\n'}---\n\n# 测试\n`)
  await writeFile(join(dir, 'skill.yaml'), manifest ?? 'scripts:\n  calc:\n    file: scripts/calc.py\n    timeout_s: 2\n    svg: true\n')
  await writeFile(join(dir, 'scripts', 'calc.py'), SCRIPT)
  await writeFile(join(dir, 'scripts', '_lib.py'), 'def double(x):\n    return 2 * x\n')
  await writeFile(join(dir, 'references', 'params.yaml'), 'status: placeholder\nk: 1.5\n')
  return { root, dir }
}

describe('技能读取', () => {
  it('读 frontmatter、正文和登记的脚本', async () => {
    const { root } = await makeSkill()
    const s = await loadSkill(root, 'demo-skill')
    assert.equal(s.version, '0.1.0')
    assert.match(s.body, /# 测试/)
    assert.equal(s.scripts.calc?.timeoutS, 2)
  })

  for (const [manifest, msg] of [
    ['scripts:\n  calc:\n    file: ../../etc/passwd\n', /scripts\//],
    ['scripts:\n  calc:\n    file: scripts/../../x.py\n', /scripts\//],
    ['scripts:\n  Calc:\n    file: scripts/calc.py\n', /脚本名/],
  ] as const) {
    it(`拒绝不合规的登记：${manifest.split('\n')[1]?.trim()}`, async () => {
      const { root } = await makeSkill(manifest)
      await assert.rejects(loadSkill(root, 'demo-skill'), msg)
    })
  }

  it('frontmatter 缺字段、名称不符', async () => {
    await assert.rejects(loadSkill((await makeSkill(undefined, 'name: demo-skill\ndescription: x\n')).root, 'demo-skill'), SkillError)
    await assert.rejects(loadSkill((await makeSkill(undefined, 'name: other\ndescription: x\nwhenToUse: y\nversion: 0.1.0\n')).root, 'demo-skill'), /目录名/)
    await assert.rejects(loadSkill((await makeSkill()).root, '../x'), /技能名不合法/)
  })
})

describe('runScript（规则与 Python 运行器相同）', () => {
  it('正常：不带凭据和 PYTHON* 变量，不写 __pycache__，带脚本和输入哈希', async () => {
    process.env.DEEPSEEK_API_KEY = 'sk-x'
    process.env.RESCUE_MCP_TOKEN = 't'
    try {
      const { root, dir } = await makeSkill()
      const spec = (await loadSkill(root, 'demo-skill')).scripts.calc!
      const r = await runScript(spec, { x: 2 }, { python: PYTHON, runDir: join(root, 'run') })
      assert.equal(r.status, 'ok', r.detail)
      assert.deepEqual(r.output?.result, { value: 6, env: [] })
      assert.equal(r.svg, '<svg/>')
      assert.match(r.scriptSha256 ?? '', /^[0-9a-f]{64}$/)
      assert.deepEqual((await readdir(join(dir, 'scripts'))).sort(), ['_lib.py', 'calc.py'])
    } finally {
      delete process.env.DEEPSEEK_API_KEY
      delete process.env.RESCUE_MCP_TOKEN
    }
  })

  for (const [mode, status] of [
    ['sleep', 'timeout'],
    ['fail', 'exit'],
    ['badjson', 'invalid-output'],
    ['nokeys', 'schema'],
    ['nosvg', 'schema'],
  ] as const) {
    it(`${mode} → ${status}`, async () => {
      const { root } = await makeSkill()
      const spec = (await loadSkill(root, 'demo-skill')).scripts.calc!
      const r = await runScript(spec, { x: 2, mode }, { python: PYTHON, runDir: join(root, 'run'), timeoutS: mode === 'sleep' ? 1 : undefined })
      assert.equal(r.status, status, r.detail)
      if (mode === 'fail') assert.match(r.detail, /BT-1201-G1/)
    })
  }

  it('解释器不存在 → spawn-error；取消 → 终止', async () => {
    const { root } = await makeSkill()
    const spec = (await loadSkill(root, 'demo-skill')).scripts.calc!
    assert.equal((await runScript(spec, { x: 1 }, { python: join(root, 'no-python'), runDir: join(root, 'r1') })).status, 'spawn-error')
    const ac = new AbortController()
    setTimeout(() => ac.abort(), 200)
    const t0 = Date.now()
    const r = await runScript(spec, { x: 1, mode: 'sleep' }, { python: PYTHON, runDir: join(root, 'r2'), signal: ac.signal })
    assert.equal(r.status, 'timeout')
    assert.ok(Date.now() - t0 < 2000)
  })
})
