/**
 * rescue-adapter：业务代码只从这里引用 DSH 的类型和函数。
 *
 * DSH 仍处于开发者预览阶段，升级时如果接口变化，只改这个文件（以及必要时的少量胶水代码），
 * 业务逻辑（src/*\/policy.ts、src/*\/record.ts）不依赖 DSH，可独立测试。
 *
 * 对应 DSH 版本：0.2.1-rc.4（见 package.json engines.dsh）。
 */

// 引入 dsh-tools 的类型声明合并，让 ctx.tools 和 'tools/*' 事件有类型
import type {} from '@deepseek-ai/dsh-tools'

export type { Context } from '@deepseek-ai/cordis'
export type {
  PreToolDecision,
  ToolExecution,
  ToolExecutionResult,
} from '@deepseek-ai/dsh-tools'
export { default as Schema } from '@deepseek-ai/schemastery'
export { Service } from '@deepseek-ai/cordis'
export { defineTool } from '@deepseek-ai/dsh-tools'
