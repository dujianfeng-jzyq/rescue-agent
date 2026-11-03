/**
 * 证据的核心逻辑（不依赖 DSH，单独测试）：采集证据、运行技能脚本、读取和导出记录。
 * 插件入口 index.ts 把它包成 cordis 服务 `rescueEvidence`，供固定流程插件在进程内调用，并注册给模型的只读工具。
 *
 * 插件本身不含领域判据：采什么数据（collect.yaml）、怎么算（scripts/）都在技能目录里（design-v2 4.3）。
 * TODO(P1)：renderReport（技能模板 + 记录 → 报告记录）；check_mine 等工具。
 */

import { createHash, randomInt } from 'node:crypto'
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises'
import { join, relative } from 'node:path'

import { collect, parseCollectSpec, type CallTool } from './collect.ts'
import { buildRecord, RECORD_ID_PATTERN, RecordNotFound, RecordStore, type Origin, type RecordFile, type RescueRecord } from './records.ts'
import { loadSkill, runScript, type RunResult } from './skills.ts'

export interface EvidenceConfig {
  skillsDir: string
  recordDir: string
  /** 脚本运行目录（输入、输出、图），缺省为记录目录旁的 runs/ */
  runDir?: string
  /** 运行技能脚本的解释器 */
  python: string
  /** mine_data 的工具调用（证据插件自带的 MCP 客户端） */
  mineData: CallTool
}

/** 技能脚本没有得到合格的结果：把运行状态原样带出，调用方按 4.5 第 3 条报告 */
export class ScriptFailed extends Error {
  readonly run: RunResult
  constructor(skill: string, script: string, run: RunResult) {
    super(`技能脚本 ${skill}/${script} 未得到结果（${run.status}）：${run.detail}`)
    this.name = 'ScriptFailed'
    this.run = run
  }
}

const SESSION_KEY = /^[A-Za-z0-9._-]{1,128}$/

export class Evidence {
  readonly store: RecordStore
  private readonly cfg: EvidenceConfig

  constructor(cfg: EvidenceConfig) {
    this.cfg = cfg
    this.store = new RecordStore(cfg.recordDir)
  }

  private filesDir(recordId: string): string {
    return join(this.cfg.recordDir, 'files', recordId)
  }

  /** 按技能的采集清单取数，存为证据记录 */
  async collectEvidence(opts: { skill: string; kind: string; panel: string; origin: Origin }): Promise<RescueRecord> {
    const skill = await loadSkill(this.cfg.skillsDir, opts.skill)
    const specPath = join(skill.dir, 'references', 'collect.yaml')
    const specText = await readFile(specPath, 'utf8')
    const ev = await collect(this.cfg.mineData, parseCollectSpec(specText, `${opts.skill}/references/collect.yaml`), opts.kind, opts.panel)
    return this.store.put(
      buildRecord({
        kind: 'evidence',
        panel: opts.panel,
        asOf: ev.as_of,
        origin: opts.origin,
        source: {
          skill: skill.name,
          skill_version: skill.version,
          collect_kind: opts.kind,
          collect_spec_sha256: createHash('sha256').update(specText).digest('hex'),
        },
        content: ev as unknown as Record<string, unknown>,
      }),
    )
  }

  /**
   * 运行技能脚本，存为结果记录。脚本的输入只能来自记录：input.json = { <名>: <记录内容>, ..., options }。
   * 结果记录的 panel、as_of 取第一个输入记录的。
   */
  async runScript(opts: {
    skill: string
    script: string
    inputs: Record<string, string>
    options?: Record<string, unknown>
    origin: Origin
    signal?: AbortSignal
  }): Promise<{ record: RescueRecord; run: RunResult }> {
    const skill = await loadSkill(this.cfg.skillsDir, opts.skill)
    const spec = skill.scripts[opts.script]
    if (!spec) throw new Error(`技能 ${opts.skill} 没有登记脚本 ${opts.script}（有：${Object.keys(skill.scripts).join('、') || '无'}）`)
    const names = Object.keys(opts.inputs)
    if (!names.length) throw new Error('技能脚本至少要有一个输入记录')
    const records = await Promise.all(names.map((n) => this.store.get(opts.inputs[n]!)))
    const input: Record<string, unknown> = Object.fromEntries(names.map((n, i) => [n, records[i]!.content]))
    if (opts.options) input.options = opts.options
    const first = records[0]!
    const runId = `${opts.skill}.${opts.script}.${Date.now()}-${randomInt(1e9).toString(36)}`
    const runDir = join(this.cfg.runDir ?? join(this.cfg.recordDir, '..', 'runs'), runId)
    const run = await runScript(spec, input, { python: this.cfg.python, runDir, ...(opts.signal ? { signal: opts.signal } : {}) })
    if (run.status !== 'ok') throw new ScriptFailed(opts.skill, opts.script, run)

    const files: RecordFile[] = []
    if (run.svg !== undefined) {
      const buf = Buffer.from(run.svg, 'utf8')
      files.push({ name: 'chart.svg', media_type: 'image/svg+xml', size: buf.length, sha256: createHash('sha256').update(buf).digest('hex') })
    }
    const record = buildRecord({
      kind: 'result',
      panel: first.panel,
      asOf: first.as_of,
      origin: opts.origin,
      source: {
        skill: skill.name,
        skill_version: skill.version,
        script: opts.script,
        script_file: relative(skill.dir, spec.file),
        script_sha256: run.scriptSha256,
        inputs: opts.inputs,
        options: opts.options ?? {},
        input_sha256: run.inputSha256,
        duration_ms: run.durationMs,
      },
      content: run.output!,
      files,
      parentIds: names.map((n) => opts.inputs[n]!),
    })
    if (files.length) {
      await mkdir(this.filesDir(record.record_id), { recursive: true })
      await copyFile(join(runDir, 'chart.svg'), join(this.filesDir(record.record_id), 'chart.svg'))
    }
    return { record: await this.store.put(record), run }
  }

  getRecord(id: string): Promise<RescueRecord> {
    return this.store.get(id)
  }

  /** 读取记录附带的文件，校验大小和哈希 */
  async readRecordFile(id: string, name: string): Promise<Buffer> {
    const rec = await this.store.get(id)
    const meta = rec.files.find((f) => f.name === name)
    if (!meta) throw new RecordNotFound(`记录 ${id} 没有文件 ${name}`)
    const buf = await readFile(join(this.filesDir(id), meta.name))
    if (buf.length !== meta.size || createHash('sha256').update(buf).digest('hex') !== meta.sha256) throw new Error(`记录 ${id} 的文件 ${name} 与登记的哈希不符`)
    return buf
  }

  /**
   * 把记录写进会话工作区，供智能体在沙箱里按技能做进一步分析（结果只作参考）。
   * 工作区所有会话共用一个目录（验证清单 #22），所以按会话分子目录：records/<会话>/<编号>.json。
   */
  async exportRecord(id: string, workspace: string, sessionKey: string): Promise<{ path: string; files: string[] }> {
    if (!RECORD_ID_PATTERN.test(id)) throw new RecordNotFound(`记录编号不合法：${id}`)
    if (!SESSION_KEY.test(sessionKey)) throw new Error(`会话编号不合法：${sessionKey}`)
    const rec = await this.store.get(id)
    const dir = join(workspace, 'records', sessionKey)
    await mkdir(dir, { recursive: true })
    const path = join(dir, `${id}.json`)
    await writeFile(path, JSON.stringify(rec, null, 2), 'utf8')
    const files: string[] = []
    for (const f of rec.files) {
      const target = join(dir, `${id}.${f.name}`)
      await writeFile(target, await this.readRecordFile(id, f.name))
      files.push(target)
    }
    return { path, files }
  }
}
