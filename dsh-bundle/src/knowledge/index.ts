/**
 * rescue-knowledge：知识库插件（docs/design-v2.md 4.7，ADR 0007）。取代 kb MCP 的检索和查条（kb 服务 P4 后停用）。
 *
 * 提供 cordis 服务 rescueKnowledge（固定流程在进程内批量取条文），并给模型注册只读工具：
 * - kb_get_articles：按文档和条号批量取完整条文（条文索引，不经 RAGFlow，毫秒级）；
 * - kb_search：语义检索法规、预案、案例（RAGFlow），结果标注能取全文的条号；RAGFlow 不可用时降级到条文索引的关键词检索；
 * - kb_list_datasets：可检索的数据集和条文索引的范围。
 * 维护工具（上传、解析、建索引）本次不开放给模型（第五稿审定第 7 条），条文索引用 scripts/kb/ 离线建立。
 * 要被固定流程注入，须和证据插件放在同一个带 isolate 的 cordis:group 里。
 */

import type { Context } from '../adapter/dsh.ts'
import { defineTool, Schema, Service } from '../adapter/dsh.ts'
import { ArticleStore } from './articles.ts'
import { DEFAULT_SYNONYMS, type DatasetKind, Knowledge, KINDS } from './service.ts'

export interface Config {
  indexPath: string
  ragflowUrl?: string
  ragflowApiKey?: string
  datasetsRegulations: string[]
  datasetsPlans: string[]
  datasetsCases: string[]
  similarityThreshold: number
  pageSize: number
  timeoutMs: number
  retries: number
}

const jsonOutput = {
  schema: { type: 'json' as const },
  render: (_args: unknown, value: unknown) => [{ type: 'text' as const, text: JSON.stringify(value) }],
}
const toJson = (v: unknown) => JSON.parse(JSON.stringify(v)) as never

declare module '@deepseek-ai/cordis' {
  interface Context {
    rescueKnowledge: RescueKnowledge
  }
}

export default class RescueKnowledge extends Service {
  static inject = ['tools']

  static Config: Schema<Config> = Schema.object({
    indexPath: Schema.string().required(),
    ragflowUrl: Schema.string(),
    ragflowApiKey: Schema.string(),
    datasetsRegulations: Schema.array(Schema.string()).default([]),
    datasetsPlans: Schema.array(Schema.string()).default([]),
    datasetsCases: Schema.array(Schema.string()).default([]),
    similarityThreshold: Schema.number().default(0.2),
    pageSize: Schema.number().default(6),
    timeoutMs: Schema.number().default(15_000),
    retries: Schema.number().default(2),
  })

  readonly knowledge: Knowledge

  constructor(ctx: Context, config: Config) {
    super(ctx, 'rescueKnowledge')
    this.knowledge = new Knowledge(ArticleStore.load(config.indexPath), {
      ragflow: {
        baseUrl: config.ragflowUrl ?? '',
        apiKey: config.ragflowApiKey ?? '',
        similarityThreshold: config.similarityThreshold,
        pageSize: config.pageSize,
        timeoutMs: config.timeoutMs,
        retries: config.retries,
      },
      datasets: { regulations: config.datasetsRegulations, plans: config.datasetsPlans, cases: config.datasetsCases },
      synonyms: DEFAULT_SYNONYMS,
    })
    const c = ctx as any
    const k = this.knowledge

    c.tools.register(
      defineTool({
        name: 'kb_get_articles',
        description:
          '按文档名和条号批量取规程条文的完整原文（来自条文索引，不经检索，跨切片的条文也完整）。' +
          '例如 document="矿山救援规程", articles=["第85条","第164条"]；parts 只取某几项，如 ["（四）"]。' +
          '引用条文时照抄返回的 document、article 和 content；source 为 not-found 时说明没有这条，不要凭记忆补写。' +
          '只收录按“第X条”组织的法规、规程、细则（kb_list_datasets 列出范围），标准和预案用 kb_search 检索。',
        parameters: {
          document: { type: 'string', required: true, description: '文档名，如 矿山救援规程、煤矿防灭火细则' },
          articles: { type: 'array', required: true, items: { type: 'string' }, description: '条号，如 ["第85条","第八十七条"]' },
          parts: { type: 'array', items: { type: 'string' }, description: '只取这几项，如 ["（三）","（四）"]；不给取全文' },
        },
        output: jsonOutput,
        execute: async (args: { document: string; articles: string[]; parts?: string[] }) =>
          toJson(k.getArticles(String(args.document), (args.articles ?? []).map(String), args.parts?.map(String))),
      }),
    )

    c.tools.register(
      defineTool({
        name: 'kb_search',
        description:
          '语义检索知识库：kind=regulations（法规规程，含标准）、plans（本矿应急预案、现场处置方案）、cases（事故调查报告）。' +
          '结果的 articles 列出片段里能取全文的条号，引用条文时用 kb_get_articles 取原文再照抄。' +
          'status 为 fallback 表示检索服务不可用、改用了条文索引的关键词检索，回答里要注明。',
        parameters: {
          query: { type: 'string', required: true, description: '检索问题或关键词' },
          kind: { type: 'string', enum: KINDS, description: '缺省 regulations' },
          limit: { type: 'number', description: '返回条数，缺省 6' },
        },
        output: jsonOutput,
        execute: async (args: { query: string; kind?: DatasetKind; limit?: number }, exec: any) =>
          toJson(await k.search((args.kind ?? 'regulations') as DatasetKind, String(args.query), Math.min(Math.max(Number(args.limit ?? 6), 1), 20), exec.signal)),
      }),
    )

    c.tools.register(
      defineTool({
        name: 'kb_list_datasets',
        description: '列出可检索的数据集（法规规程、本矿预案、事故案例）和条文索引收录的文档及条数（照抄，不要自己数）。',
        parameters: {},
        output: jsonOutput,
        execute: async (_args: unknown, exec: any) => toJson(await k.listDatasets(exec.signal)),
      }),
    )
  }
}
