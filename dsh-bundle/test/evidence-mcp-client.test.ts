import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { McpClient, McpError } from '../src/evidence/mcp-client.ts'

type Handler = (body: any, headers: Headers, signal?: AbortSignal) => Response | Promise<Response>

function client(handler: Handler, opts: { url?: string; token?: string; timeoutMs?: number } = {}) {
  const fetch = (async (_url: string, init: RequestInit) => handler(JSON.parse(String(init.body)), new Headers(init.headers), init.signal ?? undefined)) as unknown as typeof globalThis.fetch
  return new McpClient({ server: 'mine_data', url: 'url' in opts ? opts.url : 'http://x/mcp', token: opts.token, timeoutMs: opts.timeoutMs ?? 1000, fetch })
}

const json = (v: unknown, init: ResponseInit = {}) => new Response(JSON.stringify(v), { headers: { 'content-type': 'application/json', 'mcp-session-id': 's1' }, ...init })

function server(onCall: (params: any) => any): Handler {
  return (body, headers) => {
    if (body.method === 'initialize') return json({ jsonrpc: '2.0', id: body.id, result: { protocolVersion: '2025-06-18', capabilities: {} } })
    if (body.method === 'notifications/initialized') return new Response(null, { status: 202 })
    assert.equal(headers.get('mcp-session-id'), 's1')
    return json({ jsonrpc: '2.0', id: body.id, result: onCall(body.params) })
  }
}

async function kindOf(p: Promise<unknown>): Promise<string> {
  try {
    await p
    return 'ok'
  } catch (err) {
    assert.ok(err instanceof McpError, String(err))
    return err.kind
  }
}

describe('McpClient', () => {
  it('返回结构化结果，带令牌', async () => {
    let auth: string | null = null
    const c = client((b, h) => {
      auth = h.get('authorization')
      return server(() => ({ structuredContent: { now: '2026-11-07T08:00:00' }, content: [] }))(b, h)
    }, { token: 'tk' })
    assert.deepEqual(await c.call('get_clock', {}), { now: '2026-11-07T08:00:00' })
    assert.equal(auth, 'Bearer tk')
  })

  it('解析 SSE 响应', async () => {
    const c = client((b, h) => {
      if (b.method !== 'tools/call') return server(() => ({}))(b, h)
      const msg = JSON.stringify({ jsonrpc: '2.0', id: b.id, result: { structuredContent: { result: [1, 2] } } })
      return new Response(`event: message\ndata: ${msg}\n\n`, { headers: { 'content-type': 'text/event-stream' } })
    })
    assert.deepEqual(await c.call('get_latest', {}), { result: [1, 2] })
  })

  it('错误分类：未配置、鉴权、不可用、业务拒绝、超时', async () => {
    assert.equal(await kindOf(client(() => json({}), { url: '' }).call('get_clock', {})), 'unconfigured')
    assert.equal(await kindOf(client(() => new Response('', { status: 401 })).call('get_clock', {})), 'auth')
    assert.equal(await kindOf(client(() => { throw new TypeError('fetch failed') }).call('get_clock', {})), 'unavailable')
    assert.equal(await kindOf(client(() => new Response('', { status: 502 })).call('get_clock', {})), 'unavailable')
    const rejected = client(server(() => ({ isError: true, content: [{ type: 'text', text: '未知测点：X' }] })))
    await assert.rejects(rejected.call('get_latest', {}), /未知测点/)
    assert.equal(await kindOf(rejected.call('get_latest', {})), 'rejected')
    // 与真实 fetch 一样，信号中止时拒绝。AbortSignal.timeout 的计时器不保持事件循环（真实请求有连接保持），这里用一个计时器代替
    const slow = client(
      (_b, _h, signal) =>
        new Promise<Response>((_, reject) => {
          const keep = setTimeout(() => {}, 5000)
          signal?.addEventListener('abort', () => {
            clearTimeout(keep)
            reject(signal.reason)
          })
        }),
      { timeoutMs: 50 },
    )
    assert.equal(await kindOf(slow.call('get_clock', {})), 'timeout')
  })

  it('会话失效（404）时重建一次', async () => {
    let calls = 0
    let inits = 0
    const c = client((b, h) => {
      if (b.method === 'initialize') inits++
      if (b.method === 'tools/call' && ++calls === 1) return new Response('', { status: 404 })
      return server(() => ({ structuredContent: { ok: true } }))(b, h)
    })
    assert.deepEqual(await c.call('get_clock', {}), { ok: true })
    assert.equal(inits, 2)
  })
})
