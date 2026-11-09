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
    pendingAlerts: number
    degraded: boolean
    followups: { id: string; dueAt: string; expect: string; state: string }[]
    pendingApprovals: { key: string; tool: string; name: string; confirmer: string | null; timeoutMin: number | null; askedWall: string; escalated: boolean }[]
    timeline: TimelineItem[]
  }
  openIncidents: number
  evacuation: null | {
    recordId: string
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
      arrivalMin: number
      route: { reachable: boolean; targetName: string | null; lengthM: number | null; walkMin: number | null; selfRescuerMin: number | null; note: string | null; drawn: boolean; missing: string[] }
    }[]
  }
  readings: { panel: string | null; rows: ReadingRow[]; error?: string }
  alarms: { items: { id: string; sensorId: string | null; sensor: string; measurand: string | null; level: string; value: number | null; threshold: number | null; unit: string; message: string; raisedAt: string; anchor: string | null }[]; error?: string }
  fireAreas: { items: { id: string; panel: string; name: string; status: string; sealedAt: string; anchor: string | null }[]; error?: string }
  records: { id: string; kind: string; kindName: string; panel: string | null; asOf: string; createdAt: string }[]
  agent: { state: 'idle' | 'working' | 'waiting_approval' | 'waiting_question' | 'alert_pending' | 'degraded'; label: string; doing: string[]; source: 'live' | 'inferred' }
}
