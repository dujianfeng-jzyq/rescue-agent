import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { ArticleStore, articleNumber } from '../src/knowledge/articles.ts'
import { DEFAULT_SYNONYMS, Knowledge, rewrite } from '../src/knowledge/service.ts'

const store = ArticleStore.load(new URL('../data/article-index.json', import.meta.url).pathname)
const RAG = { baseUrl: 'http://ragflow.test', apiKey: 'k', similarityThreshold: 0.2, pageSize: 6, timeoutMs: 1000, retries: 1 }
const DATASETS = { regulations: ['ds-reg'], plans: ['ds-plan'], cases: [] }

function fakeFetch(handler: (url: string, init: any) => unknown): typeof fetch {
  return (async (url: any, init: any) => {
    const out = handler(String(url), init)
    if (out instanceof Error) throw out
    return new Response(JSON.stringify(out), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
}

describe('条文索引：查条', () => {
  it('批量取完整条文，含各项；单次查条远小于 1 秒', () => {
    const t0 = performance.now()
    const [a85, a164] = store.get('矿山救援规程', ['第85条', '第一百六十四条'])
    const ms = performance.now() - t0
    assert.ok(ms < 50, `查条用了 ${ms} ms`)
    assert.equal(a85!.source, 'index')
    assert.equal(a85!.article, '第85条')
    assert.equal(a85!.heading, '第八十五条')
    assert.match(a85!.content!, /^封闭火区应当遵守下列规定/)
    assert.match(a85!.content!, /（四）封闭有爆炸危险火区时，先采取注入惰性气体等抑爆措施/)
    assert.match(a85!.content!, /（五）[^\n]+向现场指挥部报告。$/)
    assert.match(a164!.content!, /（三）启封后3日内/)
  })

  it('跨切片的条文完整（矿山救援规程第33条有四项）', () => {
    const [a] = store.get('矿山救援规程', ['33'])
    assert.equal(a!.content!.split('\n').filter((l) => l.startsWith('（')).length, 4)
  })

  it('只取某几项；不存在的项写进说明', () => {
    const [a] = store.get('《矿山救援规程》', ['第85条'], ['（四）', '九'])
    assert.deepEqual(a!.parts, ['（四）'])
    assert.equal(a!.content!.split('\n').length, 2) // 引语 + 第（四）项
    assert.match(a!.note!, /没有这些项：九/)
  })

  it('文档名可以简写（唯一时）；不在索引里、条号不存在、条号写错都返回 not-found 和说明', () => {
    assert.equal(store.resolve('防灭火细则').name, '煤矿防灭火细则')
    const [x] = store.get('GB 47290-2026 煤矿防灭火技术规范', ['第1条'])
    assert.equal(x!.source, 'not-found')
    assert.match(x!.note!, /条文索引里没有/)
    const [y, z] = store.get('矿山救援规程', ['第999条', '第八五条X'])
    assert.equal(y!.source, 'not-found')
    assert.match(y!.note!, /没有第999条/)
    assert.equal(z!.source, 'not-found')
  })

  it('条号写法', () => {
    assert.deepEqual(['第85条', '第 85 条', '第八十五条', '85', '第一百零一条'].map(articleNumber), [85, 85, 85, 85, 101])
  })
})

describe('检索', () => {
  it('RAGFlow 结果标注能取全文的条号', async () => {
    let body: any
    const f = fakeFetch((url, init) => {
      assert.equal(url, 'http://ragflow.test/api/v1/retrieval')
      body = JSON.parse(init.body)
      return { code: 0, data: { chunks: [{ document_keyword: '矿山救援规程.pdf', content: '第一节矿井火灾事故救援\n第八十五条封闭火区应当遵守下列规定:\n（一）……', similarity: 0.81234 }] } }
    })
    const k = new Knowledge(store, { ragflow: RAG, datasets: DATASETS, synonyms: DEFAULT_SYNONYMS }, f)
    const r = await k.search('regulations', '封闭火区', 3)
    assert.equal(r.status, 'ok')
    assert.deepEqual(body.dataset_ids, ['ds-reg'])
    assert.match(body.question, /密闭/) // 查询改写补了同义词
    assert.deepEqual(r.results[0], { document: '矿山救援规程', content: '第一节矿井火灾事故救援\n第八十五条封闭火区应当遵守下列规定:\n（一）……', similarity: 0.812, articles: ['第85条'], source: 'ragflow' })
  })

  it('RAGFlow 断开时法规检索降级到条文索引的关键词检索；预案不降级', async () => {
    const f = fakeFetch(() => new TypeError('socket hang up'))
    const k = new Knowledge(store, { ragflow: RAG, datasets: DATASETS, synonyms: DEFAULT_SYNONYMS }, f)
    const r = await k.search('regulations', '启封 火区 复燃', 3)
    assert.equal(r.status, 'fallback')
    assert.match(r.detail!, /RAGFlow 不可用/)
    assert.equal(r.results[0]!.source, 'index-keyword')
    assert.ok(r.results.length <= 3 && r.results[0]!.articles.length === 1)
    const p = await k.search('plans', '火灾', 3)
    assert.equal(p.status, 'unavailable')
    assert.deepEqual(p.results, [])
  })

  it('没有配置数据集', async () => {
    const k = new Knowledge(store, { ragflow: { ...RAG, baseUrl: '' }, datasets: DATASETS, synonyms: DEFAULT_SYNONYMS })
    assert.equal((await k.search('cases', '火灾')).status, 'not-configured')
  })

  it('查询改写', () => {
    assert.equal(rewrite('注氮效果', DEFAULT_SYNONYMS), '注氮效果 注惰性气体 氮气防灭火')
  })
})
