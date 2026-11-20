/** /rescue-map/state 的返回（与 Node 侧 src/map/state.ts 的 MapState 保持一致，手工同步） */
export interface TimelineItem {
  at: string
  kind: string
  label: string
  text: string
}

export interface ReadingRow {
  sensorId: string
  anchor: string | null
  sensor: string
  measurand: string
  value: number | null
  unit: string
  quality: string
  role: string
}

export interface StateData {
  wall: string
  /** DSH 进程的启动编号：变化说明 DSH 重启过（如“新一轮演示”），刷新页面 */
  boot?: string
  /** 演示进度（演示环境）：state 为 finished 时显示“演示已结束” */
  demo?: { state: string; outcome: string; endedAt: string | null } | null
  mapWarnings?: string[]
  mine: { now: string | null; ok: boolean; error?: string }
  sentinel: { known: boolean; ageS: number | null; error: string | null }
  incident: null | {
    id: string
    panel: string | null
    status: string
    statusName: string
    summary: string
    openedAt: string
    updatedAt: string
    level: string | null
    levelName: string | null
    stage: string | null
    stageName: string | null
    alarmCount: number | null
    checkAsOf: string | null
    latestAlertId: string | null
    /** 事件所在工作面在地图上的名称；没有映射为 null */
    anchor: string | null
    pendingAlerts: number
    degraded: boolean
    followups: { id: string; dueAt: string; expect: string; state: string }[]
    pendingApprovals: { key: string; tool: string; name: string; confirmer: string | null; timeoutMin: number | null; askedWall: string; escalated: boolean }[]
    timeline: TimelineItem[]
  }
  openIncidents: number
  evacuation: null | {
    recordId: string
    noticeActionId?: string | null
    asOf: string
    panel: string
    computed: boolean
    note: string | null
    method: string | null
    totalThreatened: number | null
    totalUnderground: number | null
    ageHours: number | null
    freshness: 'fresh' | 'grey' | 'hidden' | 'unknown'
    schematic: boolean
    contaminated: { roadwayId: string; name: string; arrivalMin: number; drawn: boolean }[]
    groups: {
      roadwayId: string
      roadwayName: string
      anchor: string | null
      count: number
      /** 有害气体到达时刻（分钟）：service 取推演（推演时长内没到为 null），ours 取本系统估算 */
      arrival: { source: 'service' | 'ours'; min: number | null }
      /** 要显示的避灾路线（外部为主、我方兜底，第六版 3.1 节）：source 是来源，reason 是用本系统路线的原因 */
      route: {
        source: 'service' | 'ours'
        sourceText: string
        reason: string | null
        reachable: boolean
        targetName: string | null
        lengthM: number | null
        walkMin: number | null
        selfRescuerMin: number | null
        escapeMin: number | null
        smokeTimeS: number | null
        note: string | null
        drawn: boolean
        missing: string[]
      }
    }[]
    routeRecordId: string | null
    /** 蔓延范围显示哪一种：service 外部推演；ours 本系统估算 */
    spreadSource: 'service' | 'ours'
    /** 外部火灾推演的结果（试验性，第七版）；没有为 null */
    fireSpread?: null | {
      recordId: string
      status: 'ok' | 'unavailable'
      note: string | null
      horizonMin: number | null
      reachPpm: number | null
      dangerPpm: number | null
      roadways: { roadwayId: string; name: string; reachMin: number; dangerMin: number | null; peakPpm: number; reversed: boolean; drawn: boolean }[]
      flowChanged: string[]
      lines: string[]
    }
  }
  readings: { panel: string | null; rows: ReadingRow[]; error?: string }
  alarms: { items: { id: string; sensorId: string | null; sensor: string; measurand: string | null; level: string; value: number | null; threshold: number | null; unit: string; message: string; raisedAt: string; anchor: string | null }[]; error?: string }
  fireAreas: { items: { id: string; panel: string; name: string; status: string; sealedAt: string; anchor: string | null }[]; error?: string }
  records: { id: string; kind: string; kindName: string; title: string; panel: string | null; asOf: string; createdAt: string }[]
  agent: { state: 'idle' | 'working' | 'waiting_approval' | 'waiting_question' | 'alert_pending' | 'degraded'; label: string; doing: string[]; source: 'live' | 'inferred' }
  duty: { sessionId: string | null; lastDelivery: { id: string; wall: string; sessionId: string } | null }
}
