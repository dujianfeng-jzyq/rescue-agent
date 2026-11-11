/**
 * 自动切到值班会话：监听器的告警由唤醒插件投递到“值班会话”，操作员不用自己去会话列表里找。
 *   - 打开页面时：DSH 恢复出来的是空白会话（或什么都没有）就切到值班会话；操作员正在看别的有内容的会话则不动。
 *   - 之后每来一次新的投递（事件库里新的 delivered），就切到值班会话并提示。
 *
 * 用的是 DSH 网页端 uiWorkspace 服务（侧栏点会话走的也是它的 openSession）；字段名依赖 0.2.1-rc.4，
 * 取不到时什么都不做（退回手动从“会话”抽屉里选），不影响其他功能。
 */
import type { StateData } from './state-types.ts'

export interface UiWorkspace {
  openSession(sessionId: string): void
  mainReference?: { sessionId?: string }
  sessions?: { list?: { getSnapshot(): { phase?: string; byId?: Record<string, { blank?: boolean } | undefined> } } }
}

export interface DutyHooks {
  workspace(): UiWorkspace | undefined
  /** 切过去之后：显示聊天区、提示 */
  switched(reason: 'startup' | 'delivery'): void
}

/** 打开页面后最多等几次状态刷新让 DSH 恢复上次的会话，再按“没有会话”处理 */
const STARTUP_WAIT_POLLS = 3

export function dutyFollower(hooks: DutyHooks): (s: StateData) => void {
  let seenDelivery: string | null | undefined // undefined = 还没看过状态
  let startupDone = false
  let polls = 0

  const open = (sessionId: string, reason: 'startup' | 'delivery'): boolean => {
    const ws = hooks.workspace()
    if (!ws || typeof ws.openSession !== 'function') return false
    if (ws.mainReference?.sessionId !== sessionId) {
      try {
        ws.openSession(sessionId)
      } catch (e) {
        console.warn('[rescue-map] 切到值班会话失败：', e)
        return false
      }
    }
    hooks.switched(reason)
    return true
  }

  return (s) => {
    const duty = s.duty
    if (!duty) return
    const delivery = duty.lastDelivery
    if (seenDelivery === undefined) seenDelivery = delivery?.id ?? null
    else if (delivery && delivery.id !== seenDelivery) {
      seenDelivery = delivery.id
      startupDone = true
      open(delivery.sessionId, 'delivery')
      return
    }

    if (startupDone) return
    polls += 1
    const ws = hooks.workspace()
    if (!duty.sessionId || !ws) {
      if (polls >= STARTUP_WAIT_POLLS) startupDone = true
      return
    }
    const current = ws.mainReference?.sessionId
    const list = ws.sessions?.list?.getSnapshot()
    if (current === undefined && polls < STARTUP_WAIT_POLLS) return // DSH 可能还在恢复上次的会话
    if (current !== undefined && list?.phase !== undefined && list.phase !== 'ready' && polls < STARTUP_WAIT_POLLS) return
    const blank = current === undefined || list?.byId?.[current]?.blank === true
    startupDone = true
    if (blank) open(duty.sessionId, 'startup')
  }
}
