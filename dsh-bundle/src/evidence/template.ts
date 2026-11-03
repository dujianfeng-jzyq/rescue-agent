/**
 * 报告模板：技能 templates/ 下的 Markdown + 占位符（Mustache 的一个子集）。
 *
 * 与 Python 侧 rescue_contract.template 规则相同，两边对同一数据渲染结果逐字节相同
 * （共用样例 mcp/contract/tests/fixtures/template-cases.json，测试 test/template.test.ts）。规则见 Python 侧的说明：
 * {{a.b}} 取值（键不存在报错；null → 缺失、true → 是、false → 否、数字按 ECMAScript 写法、对象和列表报错；不转义），
 * {{#a}}…{{/a}} 区块、{{^a}}…{{/a}} 反向区块、{{! 注释 }}；只有一个区块标签的行整行去掉。
 */

export class TemplateError extends Error {}

type Node = string | { var: string } | { section: string; inverted: boolean; children: Node[] }

const TAG = /\{\{\s*([#^/!]?)\s*(.*?)\s*\}\}/gs
const STANDALONE = /^[ \t]*\{\{\s*[#^/!][^{}]*\}\}[ \t]*\r?\n?$/
const STANDALONE_TAG = /^[ \t]*(\{\{.*\}\})[ \t]*\r?\n?$/s

function stripStandalone(text: string): string {
  return text
    .split(/(?<=\n)/)
    .map((ln) => (STANDALONE.test(ln) ? ln.replace(STANDALONE_TAG, '$1') : ln))
    .join('')
}

function parse(text: string): Node[] {
  const root: Node[] = []
  const stack: [string, Node[]][] = [['', root]]
  let pos = 0
  for (const m of text.matchAll(TAG)) {
    const at = m.index!
    if (at > pos) stack.at(-1)![1].push(text.slice(pos, at))
    pos = at + m[0].length
    const kind = m[1]!
    const name = m[2]!
    if (kind === '!') continue
    if (!name) throw new TemplateError(`空标签：${m[0]}`)
    if (kind === '#' || kind === '^') {
      const node = { section: name, inverted: kind === '^', children: [] as Node[] }
      stack.at(-1)![1].push(node)
      stack.push([name, node.children])
    } else if (kind === '/') {
      if (stack.length === 1 || stack.at(-1)![0] !== name) throw new TemplateError(`区块结束标签 {{/${name}}} 没有对应的开始标签`)
      stack.pop()
    } else {
      stack.at(-1)![1].push({ var: name })
    }
  }
  if (stack.length > 1) throw new TemplateError(`区块 {{#${stack.at(-1)![0]}}} 没有结束标签`)
  if (pos < text.length) root.push(text.slice(pos))
  return root
}

const MISSING = Symbol('missing')
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const has = (o: Record<string, unknown>, k: string) => Object.prototype.hasOwnProperty.call(o, k)

function lookup(path: string, ctx: unknown[]): unknown {
  if (path === '.') return ctx.at(-1)
  const [first, ...rest] = path.split('.')
  let value: unknown = MISSING
  for (let i = ctx.length - 1; i >= 0; i--) {
    const frame = ctx[i]
    if (isObject(frame) && has(frame, first!)) {
      value = frame[first!]
      break
    }
  }
  for (const key of rest) {
    if (value === MISSING) break
    value = isObject(value) && has(value, key) ? value[key] : MISSING
  }
  if (value === MISSING) throw new TemplateError(`模板变量不存在：${path}`)
  return value
}

const falsy = (v: unknown) => v === null || v === undefined || v === false || v === '' || (Array.isArray(v) && v.length === 0)

function text(v: unknown, path: string): string {
  if (v === null || v === undefined) return '缺失'
  if (v === true) return '是'
  if (v === false) return '否'
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) throw new TemplateError(`模板变量 ${path} 不是有限数`)
    return Object.is(v, -0) ? '0' : String(v)
  }
  if (typeof v === 'string') return v
  throw new TemplateError(`模板变量 ${path} 是${Array.isArray(v) ? '列表' : '对象'}，不能直接输出`)
}

function renderNodes(nodes: Node[], ctx: unknown[], out: string[]): void {
  for (const n of nodes) {
    if (typeof n === 'string') out.push(n)
    else if ('var' in n) out.push(text(lookup(n.var, ctx), n.var))
    else {
      const v = lookup(n.section, ctx)
      if (n.inverted) {
        if (falsy(v)) renderNodes(n.children, ctx, out)
      } else if (Array.isArray(v)) {
        for (const item of v) renderNodes(n.children, [...ctx, item], out)
      } else if (!falsy(v)) {
        renderNodes(n.children, [...ctx, v], out)
      }
    }
  }
}

export function render(template: string, data: Record<string, unknown>): string {
  const out: string[] = []
  renderNodes(parse(stripStandalone(template)), [data], out)
  return out.join('')
}

/** 模板数据要用的记录字段 */
export interface ReportInput {
  record_id: string
  panel: string | null
  as_of: string
  content: unknown
}

/**
 * renderReport 交给模板的数据（与 Python 侧 rescue_contract.template.report_data 相同）：
 * {panel, as_of, as_of_text, record_ids: {名: 编号}, <名>: 记录内容, ...extra}。panel、as_of 取第一个记录的。
 */
export function reportData(records: Record<string, ReportInput>, extra: Record<string, unknown> = {}): Record<string, unknown> {
  const names = Object.keys(records)
  if (!names.length) throw new TemplateError('报告至少要有一个输入记录')
  const first = records[names[0]!]!
  const data: Record<string, unknown> = {
    panel: first.panel,
    as_of: first.as_of,
    as_of_text: first.as_of.slice(0, 19).replace('T', ' '),
    record_ids: Object.fromEntries(names.map((n) => [n, records[n]!.record_id])),
  }
  for (const n of names) data[n] = records[n]!.content
  return { ...data, ...extra }
}
