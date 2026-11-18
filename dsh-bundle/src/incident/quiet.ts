/**
 * 事件“平静”的判断（2026-11-18 用户要求：无预警时不登记跟踪，到点改为重新研判）：
 * 最近一次研判的预警等级为“无”，且这个事件里没有下发过处置。下发过处置的不算平静：封闭火区后读数回落、按读数定级为“无”，
 * 火区仍要长期观测，跟踪和效果复评照常。等级取自研判写进时间线的条目，这里不判断。
 */
import type { Entry } from './store.ts'

export const ISSUED = new Set(['issued', 'completed'])
/**
 * 算“处置”的对外动作：影响矿井现场的方案、撤人、物资调度、启封。发布预警、升级应急响应是通报，加密监测只是多看，都不算
 * （2026-11-11 演示复看：橙色选了“不采取处置”，同一轮提交的预警被当成“下发了新的处置”，复评按 2 小时排，理由也写错了）。
 */
export const TREATMENT_TOOLS = new Set(['issue_firefighting_plan', 'issue_sealing_plan', 'notify_evacuation', 'issue_dispatch_order', 'approve_unsealing'])

export const issued = (e: Entry, tools: Set<string>): boolean =>
  e.kind === 'note' && e.by === 'guard' && e.data?.type === 'action' && tools.has(String(e.data?.tool)) && ISSUED.has(String(e.data?.status))

/** 当前预警等级：时间线里最近一次记了等级的研判；没有为 null */
export function currentLevel(entries: Entry[]): string | null {
  const a = entries.findLast((e) => e.kind === 'note' && e.data?.type === 'assessment' && typeof e.data?.level === 'string')
  return a ? String(a.data!.level) : null
}

/** 无预警且没有下发过处置：不登记跟踪，跟踪到点时重新研判而不是复评处置效果 */
export function quietNoWarning(entries: Entry[]): boolean {
  return currentLevel(entries) === 'none' && !entries.some((e) => issued(e, TREATMENT_TOOLS))
}
