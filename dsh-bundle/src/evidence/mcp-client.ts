/**
 * 证据插件自带的最小 MCP 客户端（streamable-http，JSON 或 SSE 响应）。
 *
 * 插件用 ctx.tools.execute 只能调调用方 Agent 范围内的工具，而主预设不挂 mine_data，后台任务也不在某次工具调用里（验证清单 #19、#20），
 * 所以证据插件直接连 mine_data。失败按 design-v2 4.5 第 3 条分类：未配置、鉴权失败、不可用、业务拒绝、超时，不悄悄改用别的方法。
 */

export type McpErrorKind = 'unconfigured' | 'auth' | 'unavailable' | 'rejected' | 'timeout' | 'protocol'

export class McpError extends Error {
  readonly kind: McpErrorKind
  readonly server: string
  constructor(kind: McpErrorKind, server: string, message: string, options?: { cause?: unknown }) {
    super(`[${server}] ${message}`, options)
    this.name = 'McpError'
    this.kind = kind
    this.server = server
  }
}

export interface McpClientOptions {
  /** 服务名，只用于报错 */
  server: string
  url: string | undefined
  token?: string | undefined
  /** 单次请求超时（毫秒） */
  timeoutMs?: number
  /** 测试用：替换 fetch */
  fetch?: typeof fetch
}

const PROTOCOL_VERSION = '2025-06-18'

export class McpClient {
  private session: string | undefined
  private ready: Promise<void> | undefined
  private seq = 0
  private readonly opts: Required<Omit<McpClientOptions, 'token' | 'url'>> & { token: string | undefined; url: string | undefined }

  constructor(opts: McpClientOptions) {
    this.opts = { timeoutMs: 30_000, fetch: globalThis.fetch.bind(globalThis), ...opts, token: opts.token || undefined, url: opts.url || undefined }
  }

  private fail(kind: McpErrorKind, message: string, cause?: unknown): never {
    throw new McpError(kind, this.opts.server, message, cause === undefined ? undefined : { cause })
  }

  private async post(body: unknown, signal?: AbortSignal): Promise<any> {
    const url = this.opts.url
    if (!url) this.fail('unconfigured', '没有配置服务地址')
    const timeout = AbortSignal.timeout(this.opts.timeoutMs)
    const fused = signal ? AbortSignal.any([signal, timeout]) : timeout
    let res: Response
    try {
      res = await this.opts.fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json, text/event-stream',
          'MCP-Protocol-Version': PROTOCOL_VERSION,
          ...(this.opts.token ? { Authorization: `Bearer ${this.opts.token}` } : {}),
          ...(this.session ? { 'mcp-session-id': this.session } : {}),
        },
        body: JSON.stringify(body),
        signal: fused,
      })
    } catch (err) {
      if (timeout.aborted) this.fail('timeout', `请求超过 ${this.opts.timeoutMs} 毫秒`, err)
      if (signal?.aborted) throw err
      this.fail('unavailable', `连接失败：${(err as Error).message}`, err)
    }
    const sid = res.headers.get('mcp-session-id')
    if (sid) this.session = sid
    if (res.status === 401 || res.status === 403) this.fail('auth', `鉴权失败（HTTP ${res.status}），检查令牌`)
    if (res.status === 404 && this.session) {
      // 会话过期：清掉，由调用方重试一次
      this.session = undefined
      this.ready = undefined
      this.fail('unavailable', '会话已失效（HTTP 404）')
    }
    if (!res.ok) this.fail('unavailable', `HTTP ${res.status}`)
    if (res.status === 202) return undefined
    const text = await res.text()
    if ((res.headers.get('content-type') ?? '').includes('text/event-stream')) {
      for (const line of text.split(/\r?\n/)) {
        if (!line.startsWith('data:')) continue
        const msg = JSON.parse(line.slice(5).trim())
        if ('result' in msg || 'error' in msg) return msg
      }
      return undefined
    }
    return text ? JSON.parse(text) : undefined
  }

  private init(signal?: AbortSignal): Promise<void> {
    this.ready ??= (async () => {
      const r = await this.post(
        {
          jsonrpc: '2.0',
          id: ++this.seq,
          method: 'initialize',
          params: { protocolVersion: PROTOCOL_VERSION, capabilities: {}, clientInfo: { name: 'rescue-evidence', version: '0.1.0' } },
        },
        signal,
      )
      if (r?.error) this.fail('protocol', `initialize 失败：${r.error.message}`)
      await this.post({ jsonrpc: '2.0', method: 'notifications/initialized' }, signal)
    })().catch((err) => {
      this.ready = undefined
      this.session = undefined
      throw err
    })
    return this.ready
  }

  /**
   * 调用工具，返回结构化结果（structuredContent）。MCP 对返回列表的工具包一层 {result: [...]}，这里原样返回，由调用方取。
   * 工具报错（isError）按“业务拒绝”抛出，带上服务给的说明。
   */
  async call(tool: string, args: Record<string, unknown>, signal?: AbortSignal): Promise<any> {
    for (let attempt = 0; ; attempt++) {
      try {
        await this.init(signal)
        const r = await this.post({ jsonrpc: '2.0', id: ++this.seq, method: 'tools/call', params: { name: tool, arguments: args } }, signal)
        if (r?.error) this.fail('protocol', `${tool}：${r.error.message}`)
        const result = r?.result
        if (!result) this.fail('protocol', `${tool}：没有返回结果`)
        if (result.isError) this.fail('rejected', `${tool}：${result.content?.[0]?.text ?? '工具报错'}`)
        if (result.structuredContent !== undefined) return result.structuredContent
        const text = result.content?.[0]?.text
        return text ? JSON.parse(text) : undefined
      } catch (err) {
        // 会话失效时重建一次
        if (attempt === 0 && err instanceof McpError && err.kind === 'unavailable' && err.message.includes('会话已失效')) continue
        throw err
      }
    }
  }
}
