/**
 * RAGFlow 检索接口的最小客户端（POST /api/v1/retrieval、GET /api/v1/datasets）。由 mcp/kb 的 ragflow.py 移植。
 * 只读请求；连接被断开（网关约 5 秒超时）、超时时退避重试，HTTP 错误不重试。失败抛 RagflowError，由调用方降级。
 */

export interface RagflowConfig {
  baseUrl: string
  apiKey: string
  similarityThreshold: number
  pageSize: number
  timeoutMs: number
  retries: number
}

export interface RagflowChunk {
  document: string
  content: string
  similarity: number | null
  dataset_id: string | null
  document_id: string | null
}

export type RagflowErrorKind = 'not-configured' | 'unauthorized' | 'unavailable' | 'error'

export class RagflowError extends Error {
  readonly kind: RagflowErrorKind
  constructor(message: string, kind: RagflowErrorKind) {
    super(message)
    this.name = 'RagflowError'
    this.kind = kind
  }
}

type Fetch = typeof fetch

async function request(cfg: RagflowConfig, method: 'GET' | 'POST', path: string, body?: unknown, f: Fetch = fetch, signal?: AbortSignal): Promise<any> {
  if (!cfg.baseUrl || !cfg.apiKey) throw new RagflowError('RAGFlow 未配置', 'not-configured')
  let last: unknown
  for (let attempt = 0; attempt <= cfg.retries; attempt++) {
    const timeout = AbortSignal.timeout(cfg.timeoutMs)
    try {
      const resp = await f(`${cfg.baseUrl.replace(/\/$/, '')}${path}`, {
        method,
        headers: { Authorization: `Bearer ${cfg.apiKey}`, 'Content-Type': 'application/json' },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      })
      if (resp.status === 401 || resp.status === 403) throw new RagflowError(`RAGFlow 鉴权失败（HTTP ${resp.status}）`, 'unauthorized')
      if (!resp.ok) throw new RagflowError(`RAGFlow 不可用（HTTP ${resp.status}）`, 'unavailable')
      const payload: any = await resp.json()
      if (payload.code !== 0 && payload.code !== undefined && payload.code !== null) throw new RagflowError(`RAGFlow 返回错误：${payload.message}`, 'error')
      return payload
    } catch (err) {
      if (err instanceof RagflowError || signal?.aborted) throw err
      last = err
      if (attempt < cfg.retries) await new Promise((r) => setTimeout(r, 500 * 2 ** attempt))
    }
  }
  throw new RagflowError(`RAGFlow 不可用：${(last as Error)?.message ?? last}`, 'unavailable')
}

export async function retrieve(cfg: RagflowConfig, question: string, datasetIds: string[], pageSize?: number, f?: Fetch, signal?: AbortSignal): Promise<RagflowChunk[]> {
  const payload = await request(
    cfg,
    'POST',
    '/api/v1/retrieval',
    { question, dataset_ids: datasetIds, page: 1, page_size: pageSize ?? cfg.pageSize, similarity_threshold: cfg.similarityThreshold, keyword: true, highlight: false },
    f,
    signal,
  )
  return ((payload.data ?? {}).chunks ?? []).map((c: any) => ({
    document: String(c.document_keyword ?? c.docnm_kwd ?? c.document_name ?? ''),
    content: String(c.content ?? ''),
    similarity: typeof c.similarity === 'number' ? c.similarity : null,
    dataset_id: c.dataset_id ?? c.kb_id ?? null,
    document_id: c.document_id ?? null,
  }))
}

export async function datasetNames(cfg: RagflowConfig, ids: string[], f?: Fetch, signal?: AbortSignal): Promise<Record<string, string>> {
  const out: Record<string, string> = {}
  for (const id of ids) {
    const payload = await request(cfg, 'GET', `/api/v1/datasets?id=${encodeURIComponent(id)}`, undefined, f, signal)
    const ds = (payload.data ?? [])[0]
    if (ds) out[id] = String(ds.name ?? id)
  }
  return out
}
