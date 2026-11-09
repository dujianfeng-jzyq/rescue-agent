/**
 * 智能体标记的文字：由代码从记录里取，不由模型写。
 * 记录是技能脚本的结果（content.result），按（技能，脚本）取字段；认识的取等级、阶段、报警项数这类原值，
 * 不认识的退化成“依据：记录编号”——宁可不带信息，也不猜、不编。
 */

export interface RecordLike {
  record_id: string
  kind?: string
  panel?: string | null
  source?: { skill?: string; script?: string }
  content?: { result?: Record<string, any> }
}

export interface RecordLabel {
  text: string
  /** 记录里是预警等级时给出（blue/yellow/orange/red/none），标记颜色由它决定；其余为 null */
  warningLevel: string | null
}

const KIND_NAME: Record<string, string> = { evidence: '证据', result: '研判结果', report: '报告' }

export function labelFromRecord(r: RecordLike): RecordLabel {
  const key = `${r.source?.skill ?? ''}/${r.source?.script ?? ''}`
  const res = r.content?.result ?? {}
  const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)
  switch (key) {
    case 'fire-assessment/warning': {
      const name = str(res.level_name)
      if (name) return { text: name, warningLevel: str(res.level) }
      break
    }
    case 'fire-assessment/stage': {
      const name = str(res.stage_name)
      if (name) return { text: name, warningLevel: null }
      break
    }
    case 'fire-assessment/alarms':
      if (typeof res.count === 'number') return { text: `报警 ${res.count} 项`, warningLevel: null }
      break
    case 'explosion-triangle/explosion_triangle': {
      const name = str(res.overall_name)
      if (name) return { text: `爆炸危险性 ${name}`, warningLevel: null }
      break
    }
  }
  return { text: `依据 ${KIND_NAME[String(r.kind)] ?? '记录'} ${r.record_id}`, warningLevel: null }
}
