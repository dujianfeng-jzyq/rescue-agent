// 用已安装 DSH 自带的插件包，校验预设里各插件的 config 是否符合该插件导出的 Config 模式。
// 由 scripts/check_patches.py 调用（本机装了 dsh 时）：
//   node scripts/check_plugin_configs.mjs <@deepseek-ai/dsh 包目录>   < {env, rows}
// rows: [{where, id, name, config}]；config 里的 {"$js": 表达式} 先用示例环境变量求值。
// 输出 JSON：[{where, id, status: ok|invalid|no-schema|load-error, detail}]
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'

const require = createRequire(process.argv[2] + '/package.json')
const { env, rows } = JSON.parse(readFileSync(0, 'utf8'))
Object.assign(process.env, env)

const evalJs = (v) => {
  if (Array.isArray(v)) return v.map(evalJs)
  if (v && typeof v === 'object') {
    if (typeof v.$js === 'string') return new Function('ctx', `return (${v.$js})`)({})
    return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, evalJs(x)]))
  }
  return v
}

const modules = new Map()
const out = []
for (const { where, id, name, config } of rows) {
  if (!modules.has(name)) {
    try {
      modules.set(name, await import(require.resolve(name)))
    } catch (e) {
      modules.set(name, { loadError: String(e.message).split('\n')[0] })
    }
  }
  const mod = modules.get(name)
  if (mod.loadError) { out.push({ where, id, status: 'load-error', detail: mod.loadError }); continue }
  const schema = mod.Config ?? mod.default?.Config
  if (!schema) { out.push({ where, id, status: 'no-schema', detail: config == null ? '' : '插件未导出 Config，未校验' }); continue }
  try {
    const value = evalJs(config) ?? undefined
    if (typeof schema.safeParse === 'function') {
      const r = schema.safeParse(value)
      if (!r.success) throw new Error(r.error.issues.map((i) => `${i.path.join('.')}: ${i.message}`).join('; '))
    } else {
      schema(value) // schemastery：不合法时抛错
    }
    out.push({ where, id, status: 'ok', detail: '' })
  } catch (e) {
    out.push({ where, id, status: 'invalid', detail: String(e.message).split('\n').join(' ') })
  }
}
process.stdout.write(JSON.stringify(out))
