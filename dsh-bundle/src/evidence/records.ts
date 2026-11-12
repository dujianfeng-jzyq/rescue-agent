/**
 * 记录的构造与存储，与 Python 侧 rescue_contract.records / models.Record 的格式相同（契约 0.7.0）。
 *
 * 只有 DSH 进程（证据插件）写记录目录；bash 沙箱写不了，actions 只读并重算 content_sha256。
 * 只新增不覆盖：先写临时文件，再用硬链接放到目标名（目标已存在则失败）。
 * content_sha256 只覆盖 content（source、kind、panel、as_of 不在内），只能发现内容被误改；防篡改靠写权限（见 rescue_contract.records）。
 */

import { randomInt } from 'node:crypto'
import { link, mkdir, readFile, unlink, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

import { contentSha256 } from './canonical.ts'

export type RecordKind = 'evidence' | 'result' | 'report'

export const RECORD_PREFIX: Record<RecordKind, string> = { evidence: 'EV', result: 'RS', report: 'RP' }
export const RECORD_ID_PATTERN = /^(EV|RS|RP)-\d{8}T\d{6}-[a-z0-9]{6}$/
/** 矿井本地时间，不带时区 */
export const MINE_TIME_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/

export interface Origin {
  channel: 'web' | 'api' | 'subagent' | 'eval'
  trigger: 'chat' | 'watch' | 'button' | 'flow'
  caller?: string | null
  conversation_id?: string | null
}

export interface RecordFile {
  name: string
  media_type: string
  size: number
  sha256: string
}

export interface RescueRecord {
  record_id: string
  kind: RecordKind
  panel: string | null
  as_of: string
  created_at: string
  origin: Origin
  source: Record<string, unknown>
  content: Record<string, unknown> | string
  content_sha256: string
  files: RecordFile[]
  parent_ids: string[]
}

export class RecordNotFound extends Error {}
export class RecordTampered extends Error {}

const ID_CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789'

/** <EV|RS|RP>-<矿井时间 yyyymmddThhmmss>-<6 位随机> */
export function newRecordId(kind: RecordKind, asOf: string, rand: () => number = () => randomInt(ID_CHARS.length)): string {
  if (!MINE_TIME_PATTERN.test(asOf)) throw new Error(`矿井时间格式不对：${asOf}`)
  const stamp = asOf.slice(0, 19).padEnd(19, ':00').replace(/[-:]/g, '').slice(0, 15)
  let suffix = ''
  for (let i = 0; i < 6; i++) suffix += ID_CHARS[rand()]
  return `${RECORD_PREFIX[kind]}-${stamp}-${suffix}`
}

/** 矿井时间截到秒（证据、记录里统一用这个精度） */
export function mineTimeToSeconds(t: string): string {
  if (!MINE_TIME_PATTERN.test(t)) throw new Error(`矿井时间格式不对：${t}`)
  return t.length === 16 ? `${t}:00` : t.slice(0, 19)
}

export interface NewRecord {
  kind: RecordKind
  panel: string | null
  asOf: string
  origin: Origin
  source: Record<string, unknown>
  content: Record<string, unknown> | string
  files?: RecordFile[]
  parentIds?: string[]
  /** 测试用 */
  now?: Date
  recordId?: string
}

export function buildRecord(r: NewRecord): RescueRecord {
  const asOf = mineTimeToSeconds(r.asOf)
  return {
    record_id: r.recordId ?? newRecordId(r.kind, asOf),
    kind: r.kind,
    panel: r.panel,
    as_of: asOf,
    created_at: (r.now ?? new Date()).toISOString(),
    origin: { caller: null, conversation_id: null, ...r.origin },
    source: r.source,
    content: r.content,
    content_sha256: contentSha256(r.content),
    files: r.files ?? [],
    parent_ids: r.parentIds ?? [],
  }
}

/** 结构核对：与 Python 模型的约束一致（编号格式、前缀与类型、哈希） */
export function verify(rec: RescueRecord): RescueRecord {
  if (!RECORD_ID_PATTERN.test(rec.record_id)) throw new RecordTampered(`记录编号不合法：${rec.record_id}`)
  if (!rec.record_id.startsWith(`${RECORD_PREFIX[rec.kind]}-`)) throw new RecordTampered(`记录编号 ${rec.record_id} 的前缀与类型 ${rec.kind} 不符`)
  if (!MINE_TIME_PATTERN.test(rec.as_of)) throw new RecordTampered(`as_of 不是矿井时间：${rec.as_of}`)
  const actual = contentSha256(rec.content)
  if (actual !== rec.content_sha256) throw new RecordTampered(`记录 ${rec.record_id} 的内容与 content_sha256 不符`)
  return rec
}

export class RecordStore {
  readonly dir: string
  constructor(dir: string) {
    this.dir = dir
  }

  private path(id: string): string {
    if (!RECORD_ID_PATTERN.test(id)) throw new RecordNotFound(`记录编号不合法：${id}`)
    return join(this.dir, `${id}.json`)
  }

  async put(rec: RescueRecord): Promise<RescueRecord> {
    verify(rec)
    await mkdir(this.dir, { recursive: true })
    const target = this.path(rec.record_id)
    const tmp = join(this.dir, `.${rec.record_id}.${randomInt(1e9)}.tmp`)
    await writeFile(tmp, JSON.stringify(rec, null, 2), { encoding: 'utf8', flag: 'wx' })
    try {
      await link(tmp, target)
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === 'EEXIST') throw new Error(`记录已存在，不能覆盖：${rec.record_id}`)
      throw err
    } finally {
      await unlink(tmp).catch(() => {})
    }
    return rec
  }

  async get(id: string): Promise<RescueRecord> {
    let text: string
    try {
      text = await readFile(this.path(id), 'utf8')
    } catch (err) {
      if (err instanceof RecordNotFound) throw err
      throw new RecordNotFound(`找不到记录 ${id}`)
    }
    return verify(JSON.parse(text) as RescueRecord)
  }
}
