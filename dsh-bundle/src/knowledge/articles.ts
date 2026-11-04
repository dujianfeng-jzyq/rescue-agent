/**
 * 条文索引（docs/design-v2.md 4.7）：离线由 scripts/kb/build_article_index.py 按“章—节—条—款—项”切好，
 * 这里只读、按条号查。查条不经 RAGFlow，毫秒级，条文跨切片时也是完整的。
 * 索引里没有的条返回 not-found，不用摘要冒充原文。
 */

import { readFileSync } from 'node:fs'

export interface IndexedArticle {
  number: number
  heading: string
  chapter: string | null
  section: string | null
  paragraphs: string[]
  items: { label: string; number: number | null; paragraph: number }[]
  chunk_ids: string[]
}

export interface ArticleIndex {
  version: string
  builder: string
  built_at: string
  documents: { document: string; file_name: string; article_count: number; problems: string[] }[]
  articles: Record<string, IndexedArticle[]>
  skipped: { document: string; reason: string }[]
}

export interface ArticleResult {
  document: string
  article: string
  /** 原文条号写法，如“第八十五条” */
  heading?: string
  chapter?: string | null
  section?: string | null
  /** 条文全文（或所取的款、项），每段一行 */
  content: string | null
  /** 只取了部分款项时列出所取的部分 */
  parts?: string[]
  source: 'index' | 'not-found'
  index_version: string
  note?: string
}

const CN_DIGITS: Record<string, number> = { 零: 0, 〇: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 }
const CN_UNITS: Record<string, number> = { 十: 10, 百: 100, 千: 1000 }

export function cnToInt(s: string): number | null {
  if (/^\d+$/.test(s)) return Number(s)
  let total = 0
  let cur: number | null = null
  for (const ch of s) {
    if (ch in CN_DIGITS) cur = CN_DIGITS[ch]!
    else if (ch in CN_UNITS) {
      total += (cur ?? 1) * CN_UNITS[ch]!
      cur = null
    } else return null
  }
  return s ? total + (cur ?? 0) : null
}

/** “第85条”“第 85 条”“第八十五条”“85” → 85；认不出返回 null */
export function articleNumber(s: string): number | null {
  const body = s.replace(/\s+/g, '').replace(/^第/, '').replace(/条$/, '')
  return body ? cnToInt(body) : null
}

/** 文档名规范化：去掉书名号、空白和扩展名 */
export function normDocument(s: string): string {
  return s.replace(/[《》\s]/g, '').replace(/\.(pdf|docx?|txt|md)$/i, '')
}

export class ArticleStore {
  readonly index: ArticleIndex
  private readonly byDoc: Map<string, Map<number, IndexedArticle>>

  constructor(index: ArticleIndex) {
    this.index = index
    this.byDoc = new Map(Object.entries(index.articles).map(([d, list]) => [d, new Map(list.map((a) => [a.number, a]))]))
  }

  static load(path: string): ArticleStore {
    return new ArticleStore(JSON.parse(readFileSync(path, 'utf8')) as ArticleIndex)
  }

  documents(): string[] {
    return [...this.byDoc.keys()]
  }

  /** 文档名：完全相同优先；否则唯一包含关系（“防灭火细则”→“煤矿防灭火细则”）；否则 null 并给出候选 */
  resolve(document: string): { name: string | null; candidates: string[] } {
    const want = normDocument(document)
    if (this.byDoc.has(want)) return { name: want, candidates: [] }
    const hits = this.documents().filter((d) => d.includes(want) || (want.length >= 4 && want.includes(d)))
    return hits.length === 1 ? { name: hits[0]!, candidates: [] } : { name: null, candidates: hits }
  }

  /**
   * 批量取条文。parts 只取某几项（“（三）”“三”“3”），不给取全文；所取的项不存在时说明里写明。
   */
  get(document: string, articles: string[], parts?: string[]): ArticleResult[] {
    const v = this.index.version
    const { name, candidates } = this.resolve(document)
    return articles.map((art) => {
      const n = articleNumber(art)
      const base = { document: name ?? document, article: n === null ? art : `第${n}条`, index_version: v }
      if (!name) {
        const note = candidates.length
          ? `文档名不唯一，请写全称：${candidates.join('、')}`
          : `条文索引里没有《${document}》（已建索引：${this.documents().join('、')}；标准和其他文档只能用 kb_search 检索）`
        return { ...base, content: null, source: 'not-found', note }
      }
      if (n === null) return { ...base, content: null, source: 'not-found', note: `认不出条号“${art}”，请写成“第85条”或“第八十五条”` }
      const a = this.byDoc.get(name)!.get(n)
      if (!a) return { ...base, content: null, source: 'not-found', note: `《${name}》没有第${n}条` }
      const out: ArticleResult = { ...base, heading: a.heading, chapter: a.chapter, section: a.section, content: a.paragraphs.join('\n'), source: 'index' }
      if (parts?.length) {
        const want = parts.map((p) => cnToInt(p.replace(/[（()）\s]/g, '').replace(/^第/, '').replace(/项$/, '')))
        const picked = a.items.filter((it) => want.includes(it.number))
        const missing = parts.filter((_, i) => !picked.some((it) => it.number === want[i]))
        out.content = [a.paragraphs[0], ...picked.map((it) => a.paragraphs[it.paragraph])].join('\n')
        out.parts = picked.map((it) => it.label)
        if (missing.length) out.note = `第${n}条没有这些项：${missing.join('、')}（共 ${a.items.length} 项）`
      }
      return out
    })
  }

  /** 关键词检索（RAGFlow 不可用时的兜底）：按词频打分，返回条文 */
  search(query: string, limit: number): { document: string; article: string; content: string; score: number }[] {
    const terms = query.split(/[\s，,。；;、]+/).filter((t) => t.length >= 2)
    if (!terms.length) return []
    const scored: { document: string; article: string; content: string; score: number }[] = []
    for (const [doc, list] of this.byDoc) {
      for (const a of list.values()) {
        const text = a.paragraphs.join('\n')
        const score = terms.reduce((s, t) => s + (text.split(t).length - 1), 0)
        if (score) scored.push({ document: doc, article: `第${a.number}条`, content: text, score })
      }
    }
    return scored.sort((x, y) => y.score - x.score).slice(0, limit)
  }

  /** 检索结果里出现的条文标题（行首“第X条”），能在索引里找到的列出条号 */
  annotate(document: string, content: string): string[] {
    const { name } = this.resolve(document)
    if (!name) return []
    const nums = new Set<number>()
    for (const m of content.matchAll(/(?:^|\n)\s*第\s*([0-9]+|[零〇一二两三四五六七八九十百千]+)\s*条(?!规定|的|[、，,；;。])/g)) {
      const n = cnToInt(m[1]!)
      if (n !== null && this.byDoc.get(name)!.has(n)) nums.add(n)
    }
    return [...nums].sort((a, b) => a - b).map((n) => `第${n}条`)
  }
}
