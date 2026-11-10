/**
 * 命令行：用唤醒插件的消息生成器（src/incident/wake.ts 的 composeMessage + alertStatuses）按事件库里的真实条目生成某条告警的唤醒消息。
 * 评测运行器用它构造提示词，保证评测里的消息和生产里智能体收到的完全同源（第五版：评测原来手抄一份，消息的矛盾没人发现）。
 *
 *   node tools/wake-message.ts <事件库目录> <事件编号> <告警编号>
 */

import { alertStatuses, IncidentStore } from '../src/incident/store.ts'
import { composeMessage } from '../src/incident/wake.ts'

const [dir, incidentId, alertId] = process.argv.slice(2)
if (!dir || !incidentId || !alertId) {
  console.error('用法：node tools/wake-message.ts <事件库目录> <事件编号> <告警编号>')
  process.exit(2)
}
const store = new IncidentStore(dir)
const view = await store.get(incidentId)
const entries = await store.entries(incidentId)
const alert = entries.find((e) => e.kind === 'alert' && e.event_id === alertId)
if (!alert) {
  console.error(`事件 ${incidentId} 里没有告警 ${alertId}`)
  process.exit(1)
}
process.stdout.write(composeMessage(view, [alert], alertStatuses(entries, new Set([alertId]))))
