/**
 * 人工确认的等待与结果记进事件时间线（第三版遗留项：C 级动作超时升级，docs/design-v3.md 6.3）。
 * 审批插件在弹确认前写 approval_pending，有结果后写 approval_decided（approval_key 配对）；
 * 常驻监听器扫描事件里未决的确认，超过动作分级表的批准超时就写 approval_escalated 并告警。不依赖 DSH，单独测试。
 * 事件库写入失败不影响确认本身。
 */

import { IncidentStore } from '../incident/store.ts'
import type { ActionPolicy } from './policy.ts'

export interface PendingInfo {
  incidentId: string
  tool: string
  recordIds: string[]
  policy: ActionPolicy | undefined
}

async function lastAt(store: IncidentStore, id: string): Promise<string> {
  const entries = await store.entries(id)
  return entries[entries.length - 1]!.at
}

export async function recordPending(store: IncidentStore, key: string, info: PendingInfo): Promise<void> {
  await store.append(info.incidentId, await lastAt(store, info.incidentId), 'note', 'approval', {
    type: 'approval_pending',
    approval_key: key,
    tool: info.tool,
    record_ids: info.recordIds,
    confirmer: info.policy?.confirmer ?? null,
    timeout_min: info.policy?.approval_timeout_min ?? null,
    escalate_to: info.policy?.escalate_to ?? null,
  })
}

export async function recordDecided(store: IncidentStore, key: string, info: PendingInfo, outcome: string): Promise<void> {
  await store.append(info.incidentId, await lastAt(store, info.incidentId), 'note', 'approval', { type: 'approval_decided', approval_key: key, tool: info.tool, outcome })
}
