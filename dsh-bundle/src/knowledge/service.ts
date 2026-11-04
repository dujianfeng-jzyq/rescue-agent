/**
 * 知识库的核心逻辑（不依赖 DSH，单独测试）：查条走条文索引，检索走 RAGFlow，检索结果标注能取全文的条号。
 * RAGFlow 不可用时检索降级到条文索引的关键词检索（结果标明 source），查条不受影响。
 */

import { ArticleStore, type ArticleResult } from './articles.ts'
import { datasetNames, RagflowError, retrieve, type RagflowConfig } from './ragflow.ts'

export type DatasetKind = 'regulations' | 'plans' | 'cases'
export const KINDS: DatasetKind[] = ['regulations', 'plans', 'cases']
const KIND_NAMES: Record<DatasetKind, string> = { regulations: '法规规程', plans: '本矿预案', cases: '事故案例' }

export interface KnowledgeConfig {
  ragflow: RagflowConfig
  datasets: Record<DatasetKind, string[]>
  /** 查询改写：补充行业同义词（由 mcp/kb 迁移） */
  synonyms: Record<string, string[]>
}

export const DEFAULT_SYNONYMS: Record<string, string[]> = {
  上隅角: ['回风隅角'],
  束管: ['束管监测', '气体分析'],
  注氮: ['注惰性气体', '氮气防灭火'],
  自燃: ['自然发火', '煤自燃'],
  封闭: ['密闭', '隔绝灭火'],
  启封: ['打开密闭', '火区启封'],
}

export interface SearchHit {
  document: string
  content: string
  similarity: number | null
  /** 结果里能在条文索引里找到的条号；完整原文用 kb_get_articles 取 */
  articles: string[]
  source: 'ragflow' | 'index-keyword'
}

export interface SearchResult {
  query: string
  kind: DatasetKind
  status: 'ok' | 'fallback' | 'not-configured' | 'unavailable'
  detail?: string
  results: SearchHit[]
  hint?: string
}

export function rewrite(query: string, synonyms: Record<string, string[]>): string {
  const extra = Object.entries(synonyms).flatMap(([k, ws]) => (query.includes(k) ? ws.filter((w) => !query.includes(w)) : []))
  return extra.length ? `${query} ${extra.join(' ')}` : query
}

export class Knowledge {
  readonly articles: ArticleStore
  private readonly cfg: KnowledgeConfig
  private readonly f: typeof fetch

  constructor(articles: ArticleStore, cfg: KnowledgeConfig, f: typeof fetch = fetch) {
    this.articles = articles
    this.cfg = cfg
    this.f = f
  }

  getArticles(document: string, articles: string[], parts?: string[]): { index_version: string; results: ArticleResult[] } {
    return { index_version: this.articles.index.version, results: this.articles.get(document, articles, parts) }
  }

  async search(kind: DatasetKind, query: string, limit = 6, signal?: AbortSignal): Promise<SearchResult> {
    const ids = this.cfg.datasets[kind] ?? []
    const hint = '结果里 articles 列出的条号可以用 kb_get_articles 取完整原文；引用条文以 kb_get_articles 的原文为准。'
    let status: SearchResult['status'] = 'ok'
    let detail: string | undefined
    if (ids.length) {
      try {
        const chunks = await retrieve(this.cfg.ragflow, rewrite(query, this.cfg.synonyms), ids, limit, this.f, signal)
        const seen = new Set<string>()
        const results: SearchHit[] = []
        for (const c of chunks) {
          const key = `${c.document}\u0000${c.content.slice(0, 80)}`
          if (seen.has(key)) continue
          seen.add(key)
          results.push({ document: c.document.replace(/\.(pdf|docx?)$/i, ''), content: c.content, similarity: c.similarity === null ? null : Math.round(c.similarity * 1000) / 1000, articles: this.articles.annotate(c.document, c.content), source: 'ragflow' })
        }
        return { query, kind, status, results, hint }
      } catch (err) {
        if (!(err instanceof RagflowError)) throw err
        status = err.kind === 'not-configured' ? 'not-configured' : 'unavailable'
        detail = err.message
      }
    } else {
      status = 'not-configured'
      detail = `没有配置${KIND_NAMES[kind]}数据集`
    }
    // 降级：只有法规规程有条文索引可以兜底
    if (kind !== 'regulations') return { query, kind, status, ...(detail ? { detail } : {}), results: [] }
    const local = this.articles.search(rewrite(query, this.cfg.synonyms), limit)
    return {
      query,
      kind,
      status: local.length ? 'fallback' : status,
      detail: `${detail}；改用条文索引的关键词检索（只含按条切分的规程，不含标准）`,
      results: local.map((x) => ({ document: x.document, content: x.content, similarity: null, articles: [x.article], source: 'index-keyword' })),
      hint,
    }
  }

  async listDatasets(signal?: AbortSignal): Promise<Record<string, unknown>> {
    const ids = KINDS.flatMap((k) => this.cfg.datasets[k] ?? [])
    let names: Record<string, string> = {}
    let status = 'ok'
    try {
      names = ids.length ? await datasetNames(this.cfg.ragflow, ids, this.f, signal) : {}
    } catch (err) {
      if (!(err instanceof RagflowError)) throw err
      status = err.kind
    }
    return {
      ragflow: status,
      datasets: KINDS.map((k) => ({ kind: k, label: KIND_NAMES[k], ids: (this.cfg.datasets[k] ?? []).map((id) => ({ id, name: names[id] ?? null })) })),
      article_index: {
        version: this.articles.index.version,
        built_at: this.articles.index.built_at,
        documents: this.articles.index.documents.map((d) => ({ document: d.document, articles: d.article_count })),
        not_indexed: this.articles.index.skipped.map((s) => s.document),
      },
    }
  }
}
