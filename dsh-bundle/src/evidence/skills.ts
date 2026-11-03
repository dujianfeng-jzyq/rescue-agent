/**
 * 读取第二版技能、运行技能脚本。规则与 Python 侧 rescue_contract.skills 相同（规范见 skills/README.md）：
 *   python -E -s -B -X utf8 <脚本> --input-file … --output-file … [--svg-out …]
 *   环境变量只传 PATH、LANG、LC_ALL、TZ（不继承 DSH 进程的模型、RAGFlow、MCP 密钥，验证清单 #22）；
 *   工作目录是本次运行的目录；超时先 SIGTERM，1 秒后 SIGKILL（整个进程组）；
 *   结果按 ok / timeout / exit / invalid-output / schema / spawn-error 分类。
 * 子进程不在 bash 沙箱里，脚本是受信代码：只能运行 skill.yaml 登记过、位于本技能 scripts/ 下的脚本。
 */

import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { isAbsolute, join, relative, resolve, sep } from 'node:path'

import { parse as parseYaml } from 'yaml'

export const PYTHON_FLAGS = ['-E', '-s', '-B', '-X', 'utf8']
export const ENV_ALLOW = ['PATH', 'LANG', 'LC_ALL', 'TZ']
export const REQUIRED_OUTPUT_KEYS = ['algorithm_version', 'params', 'warnings', 'result']
const DEFAULT_TIMEOUT_S = 30
const NAME = /^[a-z0-9][a-z0-9-]*$/
const SCRIPT_NAME = /^[a-z0-9_]+$/
const SEMVER = /^\d+\.\d+\.\d+$/

export class SkillError extends Error {}

export interface ScriptSpec {
  skill: string
  name: string
  file: string
  description: string
  timeoutS: number
  svg: boolean
}

export interface Skill {
  name: string
  dir: string
  version: string
  frontmatter: Record<string, unknown>
  body: string
  scripts: Record<string, ScriptSpec>
}

function frontmatter(text: string, where: string): { data: Record<string, unknown>; body: string } {
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n/.exec(text)
  if (!m) throw new SkillError(`${where}：缺少 YAML frontmatter`)
  const data = parseYaml(m[1]!) ?? {}
  if (typeof data !== 'object' || Array.isArray(data)) throw new SkillError(`${where}：frontmatter 不是映射`)
  return { data, body: text.slice(m[0].length) }
}

export async function loadSkill(root: string, name: string): Promise<Skill> {
  if (!NAME.test(name)) throw new SkillError(`技能名不合法：${name}`)
  const dir = join(root, name)
  const { data: fm, body } = frontmatter(await readFile(join(dir, 'SKILL.md'), 'utf8'), `${name}/SKILL.md`)
  const missing = ['name', 'description', 'whenToUse', 'version'].filter((k) => !fm[k])
  if (missing.length) throw new SkillError(`${name}/SKILL.md：frontmatter 缺少 ${missing.join(', ')}`)
  if (fm.name !== name) throw new SkillError(`${name}：目录名必须与 frontmatter 的 name（${String(fm.name)}）相同`)
  if (!SEMVER.test(String(fm.version))) throw new SkillError(`${name}：version 应为 x.y.z，现在是 ${String(fm.version)}`)
  const manifest = parseYaml(await readFile(join(dir, 'skill.yaml'), 'utf8')) ?? {}
  const scripts: Record<string, ScriptSpec> = {}
  const scriptsDir = resolve(dir, 'scripts')
  for (const [sname, spec] of Object.entries<any>(manifest.scripts ?? {})) {
    if (!SCRIPT_NAME.test(sname)) throw new SkillError(`${name}：脚本名 ${sname} 只能用小写字母、数字和下划线`)
    const rel = String(spec?.file ?? '')
    const abs = resolve(dir, rel)
    const inside = relative(scriptsDir, abs)
    if (isAbsolute(rel) || !rel.startsWith('scripts/') || inside.startsWith('..') || inside.startsWith(sep) || isAbsolute(inside)) {
      throw new SkillError(`${name}：脚本 ${sname} 必须在本技能的 scripts/ 下`)
    }
    scripts[sname] = {
      skill: name,
      name: sname,
      file: abs,
      description: String(spec.description ?? ''),
      timeoutS: Number(spec.timeout_s ?? DEFAULT_TIMEOUT_S),
      svg: Boolean(spec.svg),
    }
  }
  return { name, dir, version: String(fm.version), frontmatter: fm, body, scripts }
}

export type RunStatus = 'ok' | 'timeout' | 'exit' | 'invalid-output' | 'schema' | 'spawn-error'

export interface RunResult {
  status: RunStatus
  detail: string
  output?: Record<string, unknown>
  svg?: string
  durationMs: number
  stderr: string
  /** 写进结果记录的 source：脚本和输入的哈希 */
  scriptSha256?: string
  inputSha256?: string
}

export interface RunOptions {
  python: string
  /** 本次运行的目录（写 input.json、output.json、chart.svg）；由调用方创建在记录目录旁 */
  runDir: string
  timeoutS?: number
  signal?: AbortSignal
}

export function childEnv(): Record<string, string> {
  const env: Record<string, string> = {}
  for (const k of ENV_ALLOW) if (process.env[k] !== undefined) env[k] = process.env[k]!
  return env
}

const sha256 = (data: string | Buffer) => createHash('sha256').update(data).digest('hex')

export async function runScript(spec: ScriptSpec, input: unknown, opts: RunOptions): Promise<RunResult> {
  await mkdir(opts.runDir, { recursive: true })
  const inFile = join(opts.runDir, 'input.json')
  const outFile = join(opts.runDir, 'output.json')
  const svgFile = join(opts.runDir, 'chart.svg')
  const inputText = JSON.stringify(input)
  await writeFile(inFile, inputText, 'utf8')
  const scriptSha256 = sha256(await readFile(spec.file))
  const inputSha256 = sha256(inputText)
  const args = [...PYTHON_FLAGS, spec.file, '--input-file', inFile, '--output-file', outFile, ...(spec.svg ? ['--svg-out', svgFile] : [])]
  const timeoutMs = (opts.timeoutS ?? spec.timeoutS) * 1000
  const t0 = Date.now()

  const r = await new Promise<{ code: number | null; timedOut: boolean; spawnError?: string; stderr: string }>((done) => {
    let stderr = ''
    let timedOut = false
    let settled = false
    const finish = (v: { code: number | null; spawnError?: string }) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      opts.signal?.removeEventListener('abort', onAbort)
      done({ ...v, timedOut, stderr })
    }
    const child = spawn(opts.python, args, { cwd: opts.runDir, env: childEnv(), stdio: ['ignore', 'ignore', 'pipe'], detached: true })
    const killGroup = (sig: NodeJS.Signals) => {
      try {
        if (child.pid) process.kill(-child.pid, sig)
      } catch {
        /* 已退出 */
      }
    }
    const stop = () => {
      killGroup('SIGTERM')
      setTimeout(() => killGroup('SIGKILL'), 1000).unref()
    }
    const onAbort = () => stop()
    opts.signal?.addEventListener('abort', onAbort, { once: true })
    const timer = setTimeout(() => {
      timedOut = true
      stop()
    }, timeoutMs)
    child.stderr!.on('data', (b: Buffer) => {
      stderr = (stderr + b.toString('utf8')).slice(-4000)
    })
    child.on('error', (err) => finish({ code: null, spawnError: err.message }))
    child.on('close', (code) => finish({ code }))
  })

  const base = { durationMs: Date.now() - t0, stderr: r.stderr, scriptSha256, inputSha256 }
  if (r.spawnError) return { status: 'spawn-error', detail: `启动失败：${r.spawnError}`, ...base }
  if (r.timedOut) return { status: 'timeout', detail: `超过 ${timeoutMs / 1000} 秒，已终止`, ...base }
  if (opts.signal?.aborted) return { status: 'timeout', detail: '已取消', ...base }
  if (r.code !== 0) return { status: 'exit', detail: `退出码 ${r.code}：${r.stderr.trim().slice(-500)}`, ...base }
  let output: unknown
  try {
    output = JSON.parse(await readFile(outFile, 'utf8'))
  } catch (err) {
    return { status: 'invalid-output', detail: `输出不是 JSON：${(err as Error).message}`, ...base }
  }
  if (typeof output !== 'object' || output === null || Array.isArray(output)) {
    return { status: 'schema', detail: '输出不是 JSON 对象', ...base }
  }
  const o = output as Record<string, unknown>
  const missing = REQUIRED_OUTPUT_KEYS.filter((k) => !(k in o))
  if (missing.length) return { status: 'schema', detail: `输出缺少 ${missing.join(', ')}`, output: o, ...base }
  if (!Array.isArray(o.warnings)) return { status: 'schema', detail: '输出的 warnings 不是列表', output: o, ...base }
  let svg: string | undefined
  if (spec.svg) {
    svg = await readFile(svgFile, 'utf8').catch(() => '')
    if (!svg.trim()) return { status: 'schema', detail: '登记了 svg，但没有生成风险图', output: o, ...base }
  }
  return { status: 'ok', detail: '', output: o, ...(svg !== undefined ? { svg } : {}), ...base }
}
