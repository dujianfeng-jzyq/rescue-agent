import assert from 'node:assert/strict'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { test } from 'node:test'

import { render, TemplateError } from '../src/evidence/template.ts'

// 与 Python 侧 rescue_contract.template 共用的样例：两边渲染结果逐字节相同
const cases: { name: string; template: string; data: Record<string, unknown>; expected?: string; error?: string }[] = JSON.parse(
  readFileSync(new URL('../../mcp/contract/tests/fixtures/template-cases.json', import.meta.url), 'utf8'),
)

for (const c of cases) {
  test(`模板：${c.name}`, () => {
    if (c.error !== undefined) {
      assert.throws(() => render(c.template, c.data), (err: unknown) => err instanceof TemplateError && err.message.includes(c.error!))
    } else {
      assert.equal(render(c.template, c.data), c.expected)
    }
  })
}

// 技能模板的样例（skills/*/tests/templates/<模板>/<样例>.input.json + .expected.md）：Python 的 skills check 也跑同一批
const skills = new URL('../../skills/', import.meta.url)
for (const skill of readdirSync(skills, { withFileTypes: true }).filter((d) => d.isDirectory())) {
  const tdir = new URL(`${skill.name}/tests/templates/`, skills)
  if (!existsSync(tdir)) continue
  for (const tpl of readdirSync(tdir)) {
    const template = readFileSync(new URL(`${skill.name}/templates/${tpl}.md`, skills), 'utf8')
    for (const f of readdirSync(new URL(`${tpl}/`, tdir)).filter((n) => n.endsWith('.input.json'))) {
      const name = f.slice(0, -'.input.json'.length)
      test(`技能模板 ${skill.name}/${tpl}/${name} 与 Python 渲染一致`, () => {
        const data = JSON.parse(readFileSync(new URL(`${tpl}/${f}`, tdir), 'utf8'))
        assert.equal(render(template, data), readFileSync(new URL(`${tpl}/${name}.expected.md`, tdir), 'utf8'))
      })
    }
  }
}
