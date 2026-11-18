/**
 * 大屏的自绘部分：顶栏、左栏（矿井信息）、右栏（智能体信息）。纯 DOM，每次拿到新状态就整块重画（数据量很小）。
 * 这里只显示：预警等级、阶段、读数都是 Node 侧从事件库和矿井原样取来的，前端不算、不判断。
 */
import { openRecord } from './record-modal.ts'
import type { StateData } from './state-types.ts'

type Kid = Node | string | null | false | undefined

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs?: Record<string, string> | null, ...kids: Kid[]): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag)
  for (const [k, v] of Object.entries(attrs ?? {})) e.setAttribute(k, v)
  for (const kid of kids) if (kid !== null && kid !== false && kid !== undefined) e.append(kid)
  return e
}

const LEVEL_COLOR: Record<string, string> = { none: '#35c46a', blue: '#4aa3ff', yellow: '#f5c518', orange: '#ff8a1f', red: '#ff4d4f' }
const KIND_COLOR: Record<string, string> = { alert: '#ff8a1f', recovered: '#35c46a', degraded: '#f5c518', opened: '#4aa3ff', note: '#7fb2e6', status: '#9aa8ff' }

/** 读数质量（契约 Quality）的中文；不认识的原样显示 */
const QUALITY_NAME: Record<string, string> = { missing: '缺数', suspect: '疑似异常', sealed_off: '封闭断电' }
/** 智能体卡片里最多列几条最近记录（卡片在聊天区上方，要短） */
const MAX_RECORDS = 3

const clock = (iso: string | null | undefined): string => (iso ? iso.replace('T', ' ').slice(0, 19) : '—')
const hm = (iso: string): string => iso.slice(5, 16).replace('T', ' ')

function fmtValue(v: number | null): string {
  if (v === null) return '—'
  return Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2)
}

function section(title: string, ...kids: Kid[]): HTMLElement {
  return h('section', { class: 'rm-sec' }, h('h3', null, title), ...kids)
}

function empty(text: string): HTMLElement {
  return h('div', { class: 'rm-empty' }, text)
}

/**
 * 灾害推演、路径规划：界面上只写这两个名字，结果来自外部服务还是智能体只用颜色区分（data-kind + data-source 决定颜色，
 * 和地图上的线同色）；每节末尾一行颜色说明。
 */
type SrcKind = 'fire' | 'route'
const srcAttrs = (kind: SrcKind, source: 'service' | 'ours'): Record<string, string> => ({ 'data-kind': kind, 'data-source': source })
function sourceKey(kind: SrcKind): HTMLElement {
  return h(
    'p',
    { class: 'rm-note rm-src-key' },
    '颜色：',
    h('span', { class: 'rm-swatch', ...srcAttrs(kind, 'service') }),
    '外部服务',
    h('span', { class: 'rm-swatch', ...srcAttrs(kind, 'ours') }),
    '智能体计算',
  )
}

export interface PanelsHandle {
  update(state: StateData | null, staleS: number, failed: boolean): void
  dispose(): void
}

/**
 * onPending：点顶栏的“待确认/待回答”标记（全屏时看不到智能体卡片里的待确认指令，标记提醒并可一键展开面板）
 */
export function mountPanels(
  root: HTMLElement,
  title: string,
  onFocus: (anchor: string) => void,
  onFireTime: (t: number | null) => void = () => {},
  onPending: () => void = () => {},
): PanelsHandle {
  // 火灾推演回放（第七版）：左栏每次拿到新状态都整块重画，回放控件要跨重画复用（拖动、播放时不能被打断）；换了记录就重置
  let replay: { recordId: string; horizon: number; el: HTMLElement; stop: () => void } | null = null
  const dropReplay = (): void => {
    if (!replay) return
    replay.stop()
    replay = null
    onFireTime(null)
  }
  const replayControl = (recordId: string, horizon: number): HTMLElement => {
    if (replay && replay.recordId === recordId && replay.horizon === horizon) return replay.el
    dropReplay()
    const range = h('input', { type: 'range', min: '0', max: String(horizon), step: '1', value: String(horizon), 'aria-label': '推演回放时刻（分钟）' })
    const out = h('output', null, `共 ${horizon} 分钟`)
    const play = h('button', { type: 'button' }, '播放')
    const all = h('button', { type: 'button', title: '显示推演的全部结果' }, '全部')
    let t: number | null = null
    let timer = 0
    const set = (v: number | null): void => {
      t = v
      range.value = String(v ?? horizon)
      out.textContent = v === null ? `共 ${horizon} 分钟` : `第 ${v} 分钟`
      onFireTime(v)
    }
    const stop = (): void => {
      if (timer) window.clearInterval(timer)
      timer = 0
      play.textContent = '播放'
    }
    range.addEventListener('input', () => {
      stop()
      set(Number(range.value))
    })
    play.addEventListener('click', () => {
      if (timer) return stop()
      let k = t === null || t >= horizon ? 0 : t
      set(k)
      play.textContent = '暂停'
      timer = window.setInterval(() => {
        k += 1
        set(Math.min(k, horizon))
        if (k >= horizon) stop()
      }, 250)
    })
    all.addEventListener('click', () => {
      stop()
      set(null)
    })
    replay = { recordId, horizon, el: h('div', { class: 'rm-fire-replay' }, play, range, out, all), stop }
    return replay.el
  }

  // ── 顶栏 ────────────────────────────────────────────────────────
  const logo = h('div', { class: 'rm-logo', 'aria-hidden': 'true' })
  logo.innerHTML =
    '<svg viewBox="0 0 32 32" width="30" height="30"><path d="M16 2 3 8v8c0 7.5 5.2 12.7 13 14 7.8-1.3 13-6.5 13-14V8L16 2Z" fill="none" stroke="#4fd1ff" stroke-width="1.8"/><path d="M9 21 14 11l3 6 2-3 4 7" fill="none" stroke="#4fd1ff" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/></svg>'
  const titleEl = h('div', { class: 'rm-title' }, h('b', null, '煤矿应急救援智能体'), h('span', null, title))
  const mineClock = h('div', { class: 'rm-clock-main' }, '—')
  const wallClock = h('div', { class: 'rm-clock-sub' }, '')
  const chips = h('div', { class: 'rm-chips' })
  const toolsSlot = h('div', { class: 'rm-tools' })
  // 三段：左边状态（时间、预警等级、数据和监听），中间标题（图标、智能体、矿井名称），右边操作按钮
  const header = h(
    'header',
    { class: 'rm-header' },
    h('div', { class: 'rm-header-left' }, h('div', { class: 'rm-clock' }, mineClock, wallClock), chips),
    h('div', { class: 'rm-brand' }, logo, titleEl),
    h('div', { class: 'rm-header-right' }, toolsSlot),
  )

  // ── 左右栏 ──────────────────────────────────────────────────────
  const left = h('aside', { class: 'rm-card rm-left', 'aria-label': '矿井信息' })
  const right = h('aside', { class: 'rm-card rm-right', 'aria-label': '智能体信息' })
  root.append(header, left, right)

  const tick = (): void => {
    wallClock.textContent = `矿井时间 · 本机 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`
  }
  tick()
  const timer = window.setInterval(tick, 1000)

  const chip = (text: string, tone: 'ok' | 'warn' | 'bad' | 'dim'): HTMLElement => h('span', { class: 'rm-chip', 'data-tone': tone }, h('i'), text)

  function renderLeft(s: StateData | null, failed: boolean): void {
    const inc = s?.incident ?? null
    const color = inc?.level ? (LEVEL_COLOR[inc.level] ?? '#7fb2e6') : '#5b6b80'
    // 火区封闭后，密闭内测点断电，监听只能按束管读数判定，读数回落后会显示“无预警”；注明一句，免得被看成火已熄灭（2026-11-11 演示复看）
    const sealed = inc ? s?.fireAreas.items.find((f) => f.status === 'sealed' && f.panel === inc.panel) : undefined
    const sit = section(
      '预警态势',
      inc
        ? h(
            'div',
            { class: 'rm-situation', style: `--lv:${color}` },
            h('div', { class: 'rm-level' }, inc.level === 'none' ? '无预警' : (inc.levelName ?? '未研判')),
            sealed && h('p', { class: 'rm-note rm-sealed-note' }, `火区已封闭${sealed.sealedAt ? `（${hm(sealed.sealedAt)}）` : ''}，读数为封闭后的束管监测；等级按读数判定，不代表火已熄灭`),
            h(
              'dl',
              { class: 'rm-kv' },
              h('dt', null, '发展阶段'),
              h('dd', null, inc.stageName ?? '—'),
              h('dt', null, '工作面'),
              h('dd', null, inc.panel ?? '—'),
              h('dt', null, '事件状态'),
              h('dd', null, inc.statusName),
              h('dt', null, '报警数'),
              h('dd', null, inc.alarmCount === null ? '—' : String(inc.alarmCount)),
              h('dt', null, '检测时刻'),
              h('dd', null, clock(inc.checkAsOf)),
            ),
            h('div', { class: 'rm-badges' }, inc.pendingAlerts > 0 && h('span', { class: 'rm-badge', 'data-tone': 'warn' }, `${inc.pendingAlerts} 条告警未确认`), inc.degraded && h('span', { class: 'rm-badge', 'data-tone': 'bad' }, '监听降级')),
            inc.summary && h('p', { class: 'rm-summary' }, inc.summary),
          )
        : empty(failed ? '状态接口取不到，界面数据可能已过期' : '暂无未关闭的事件'),
    )

    const rd = s?.readings
    const readings = section(
      `关键测点${rd?.panel ? ` · ${rd.panel}` : ''}`,
      rd && rd.rows.length
        ? h(
            'div',
            { class: 'rm-sensors' },
            ...[...new Set(rd.rows.map((r) => r.sensor))].map((name) => {
              const anchor = rd.rows.find((r) => r.sensor === name)?.anchor ?? null
              const card = h(
                'div',
                { class: 'rm-sensor', ...(anchor ? { 'data-anchor': anchor, title: `在地图上定位：${anchor}`, role: 'button', tabindex: '0' } : {}) },
                h('div', { class: 'rm-sensor-name' }, name),
                h(
                  'div',
                  { class: 'rm-sensor-vals' },
                  ...rd.rows
                    .filter((r) => r.sensor === name)
                    .map((r) => h('div', { class: 'rm-val', 'data-q': r.quality }, h('span', null, r.measurand), h('b', null, fmtValue(r.value)), h('small', null, r.value === null ? (r.quality === 'ok' ? '' : (QUALITY_NAME[r.quality] ?? r.quality)) : r.unit))),
                ),
              )
              if (anchor) {
                card.addEventListener('click', () => onFocus(anchor))
                card.addEventListener('keydown', (e) => {
                  if (e.key === 'Enter' || e.key === ' ') onFocus(anchor)
                })
              }
              return card
            }),
          )
        : empty(rd?.error ?? '暂无读数'),
    )

    const al = s?.alarms
    const alarms = section(
      '矿井报警',
      al && al.items.length
        ? h(
            'ul',
            { class: 'rm-alarms' },
            ...al.items.map((a) => {
              const li = h(
                'li',
                { 'data-level': a.level, ...(a.anchor ? { 'data-anchor': a.anchor, title: `在地图上定位：${a.anchor}`, role: 'button', tabindex: '0' } : {}) },
                h('i'),
                h('span', { class: 'rm-alarm-name' }, a.sensor),
                h('b', { title: '越限时的读数' }, `${a.measurand ?? ''} ${a.value === null ? '' : fmtValue(a.value) + a.unit}`.trim()),
                h('time', null, hm(a.raisedAt)),
              )
              if (a.anchor) li.addEventListener('click', () => onFocus(a.anchor!))
              return li
            }),
          )
        : empty(al?.error ?? '没有越限的报警'),
      s && s.fireAreas.items.length
        ? h(
            'ul',
            { class: 'rm-list rm-fire' },
            ...s.fireAreas.items.map((f) => {
              const li = h('li', null, h('span', { class: 'rm-kind' }, f.status === 'sealed' ? '已封闭' : '已启封'), h('span', null, f.name), h('time', null, hm(f.sealedAt)))
              if (f.anchor) li.addEventListener('click', () => onFocus(f.anchor!))
              return li
            }),
          )
        : null,
    )

    const ev = s?.evacuation ?? null
    const FRESH_TEXT: Record<string, string> = { fresh: '', grey: '记录已超过 2 小时，仅供参考', hidden: '记录已超过 6 小时，不在图上显示', unknown: '' }
    const evac = ev
      ? section(
          '受威胁人员与撤离',
          h('p', { class: 'rm-note' }, ev.noticeActionId ? `依据撤人通知 ${ev.noticeActionId} 的记录 ${ev.recordId}（${hm(ev.asOf)}）` : `依据记录 ${ev.recordId}（${hm(ev.asOf)}）`),
          !ev.computed
            ? empty(ev.note ?? '记录没有计算受威胁人员')
            : h(
                'div',
                { class: 'rm-evac' },
                h('div', { class: 'rm-evac-total' }, h('b', null, ev.totalThreatened === null ? '—' : String(ev.totalThreatened)), h('span', null, ' 人受威胁'), ev.totalUnderground !== null && h('small', null, `（井下共 ${ev.totalUnderground} 人）`)),
                ev.freshness !== 'fresh' && FRESH_TEXT[ev.freshness] && h('p', { class: 'rm-note', 'data-tone': 'warn' }, FRESH_TEXT[ev.freshness]!),
                ...ev.groups.map((g) => {
                  const r = g.route
                  const fsv = ev.fireSpread
                  const arrival =
                    g.arrival.source === 'service'
                      ? g.arrival.min === null
                        ? `${fsv?.horizonMin ?? '—'} 分钟内 CO 未达到 ${fsv?.reachPpm ?? '—'} ppm`
                        : `第 ${g.arrival.min} 分钟 CO 达到 ${fsv?.reachPpm ?? '—'} ppm`
                      : g.arrival.min === null
                        ? ''
                        : `约 ${g.arrival.min} 分钟到达`
                  const detail =
                    r.source === 'service'
                      ? `${r.lengthM === null ? '' : `，约 ${r.lengthM} 米`}${r.escapeMin === null ? '' : `，暂时脱险约 ${r.escapeMin} 分钟`}${r.smokeTimeS ? `，烟中通行约 ${r.smokeTimeS} 秒` : ''}`
                      : `${r.lengthM === null ? '' : `，${r.lengthM} 米`}${r.walkMin === null ? '' : `，步行约 ${r.walkMin} 分钟`}${r.selfRescuerMin === null ? '' : `（佩戴自救器约 ${r.selfRescuerMin} 分钟）`}`
                  const li = h(
                    'div',
                    { class: 'rm-evac-group', 'data-blocked': r.reachable ? '0' : '1', ...(g.anchor ? { 'data-anchor': g.anchor, role: 'button', tabindex: '0', title: `在地图上定位：${g.anchor}` } : {}) },
                    h('div', null, h('b', null, `${g.count} 人`), h('span', null, ` ${g.roadwayName}`)),
                    arrival && h('div', { class: 'rm-src', ...srcAttrs('fire', g.arrival.source) }, `灾害推演：${arrival}`),
                    r.reachable
                      ? h('div', { class: 'rm-evac-route rm-src', ...srcAttrs('route', r.source) }, `路径规划：撤往 ${r.targetName ?? '—'}${detail}`)
                      : h('div', { class: 'rm-evac-route rm-src', ...srcAttrs('route', r.source), 'data-tone': 'bad' }, '路径规划：无可行路线，需人工研判'),
                    r.reachable && !r.drawn && h('div', { class: 'rm-note' }, `图上未画出：${r.missing.length ? `有 ${r.missing.length} 处没有映射` : '映射不完整'}`),
                  )
                  if (g.anchor) {
                    li.addEventListener('click', () => onFocus(g.anchor!))
                    li.addEventListener('keydown', (e) => {
                      if (e.key === 'Enter' || e.key === ' ') onFocus(g.anchor!)
                    })
                  }
                  return li
                }),
                ev.method && h('p', { class: 'rm-note' }, ev.method),
                h('p', { class: 'rm-note' }, ev.routeRecordId ? `路径规划依据记录 ${ev.routeRecordId}，输入含占位值。撤人按预案执行。` : '撤人按预案执行。'),
                h('p', { class: 'rm-note' }, `${ev.schematic ? '示意映射：' : ''}图上折线只表示走哪几段巷道，距离和时间以文字为准。`),
                sourceKey('route'),
              ),
        )
      : null

    // 灾害推演（外部为主、我方兜底，第七版 3.1 节）：标题都是“灾害推演”，来源只用颜色区分。
    // 推演可用时图上只有推演层（可按时间回放），文字照抄记录里的整句；不可用或没有推演时列本系统估算的污染巷道（图上同色）
    const fs = ev?.computed ? (ev.fireSpread ?? null) : null
    if (!fs || fs.status !== 'ok') dropReplay()
    const svc = fs?.status === 'ok' ? fs : null
    const notDrawn = svc ? svc.roadways.filter((r) => !r.drawn).map((r) => r.name) : (ev?.contaminated ?? []).filter((c) => !c.drawn).map((c) => c.name)
    const fireSec = ev?.computed
      ? section(
          '灾害推演',
          svc
            ? h(
                'div',
                { class: 'rm-fire-sim rm-src', ...srcAttrs('fire', 'service') },
                h('p', { class: 'rm-note' }, `依据记录 ${svc.recordId}；火源和参数是占位值。撤人按预案执行。`),
                svc.horizonMin !== null && svc.roadways.some((r) => r.drawn) && replayControl(svc.recordId, svc.horizonMin),
                svc.reachPpm !== null &&
                  h(
                    'div',
                    { class: 'rm-note' },
                    h('span', { class: 'rm-fire-swatch', style: 'background:#a78bfa' }),
                    `CO 达到 ${svc.reachPpm} ppm`,
                    svc.dangerPpm !== null && h('span', { class: 'rm-fire-swatch', style: 'background:#f052e6;margin-left:10px' }),
                    svc.dangerPpm !== null && `达到 ${svc.dangerPpm} ppm`,
                  ),
                h('ul', null, ...svc.lines.map((l) => h('li', null, l))),
              )
            : h(
                'div',
                { class: 'rm-fire-sim rm-src', ...srcAttrs('fire', 'ours') },
                h('p', { class: 'rm-note' }, `依据记录 ${ev.recordId}。撤人按预案执行。`),
                ev.contaminated.length
                  ? h(
                      'div',
                      null,
                      h('div', { class: 'rm-note' }, h('span', { class: 'rm-fire-swatch', style: 'background:linear-gradient(90deg,#ff4d4f,#ffb020)' }), '有害气体到达的巷道（越早到达越红）'),
                      h('ul', null, ...ev.contaminated.map((c) => h('li', null, `${c.name}：约 ${c.arrivalMin} 分钟到达`))),
                    )
                  : empty('没有有害气体到达的巷道'),
              ),
          notDrawn.length > 0 && h('p', { class: 'rm-note' }, `图上未画出（没有映射）：${notDrawn.join('、')}`),
          sourceKey('fire'),
        )
      : null

    const tl = section(
      '事件时间线',
      inc && inc.timeline.length
        ? h(
            'ol',
            { class: 'rm-timeline' },
            ...inc.timeline.map((t) => h('li', { style: `--c:${KIND_COLOR[t.kind] ?? '#7fb2e6'}` }, h('time', null, hm(t.at)), h('b', null, t.label), t.text && h('span', null, t.text))),
          )
        : empty('暂无'),
    )

    const ended = s?.demo?.state === 'finished'
    const banner = ended
      ? h('div', { class: 'rm-demo-end' }, h('b', null, '演示已结束'), h('span', null, `${s!.demo!.outcome || ''}${s!.demo!.endedAt ? `（矿井时间 ${hm(s!.demo!.endedAt)} 停止推进）` : ''}`))
      : null
    const fu = section(
      '跟踪计划',
      ...(ended && inc?.followups.some((f) => f.state !== 'done') ? [h('div', { class: 'rm-note' }, '演示已结束，矿井时钟停止，下面的跟踪不会再到点')] : []),
      inc && inc.followups.length
        ? h('ul', { class: 'rm-list' }, ...inc.followups.map((f) => h('li', null, h('time', null, hm(f.dueAt)), h('span', null, f.expect || '复评'), f.state === 'due' && h('em', null, '已到点'))))
        : empty('暂无'),
    )
    left.replaceChildren(...(banner ? [banner] : []), sit, readings, alarms, ...(evac ? [evac] : []), ...(fireSec ? [fireSec] : []), tl, fu)
  }

  const STATE_TONE: Record<string, 'ok' | 'warn' | 'bad' | 'dim'> = { idle: 'dim', working: 'ok', waiting_approval: 'warn', waiting_question: 'warn', alert_pending: 'warn', degraded: 'bad' }

  function renderRight(s: StateData | null): void {
    const a = s?.agent
    const inc = s?.incident ?? null
    // 卡片在聊天区上方，只放一眼要看的：状态一行、当前事件一行、有待确认指令时列出来、最近几条记录
    const status = section(
      '智能体状态',
      h(
        'div',
        { class: 'rm-agent', 'data-tone': a ? (STATE_TONE[a.state] ?? 'dim') : 'dim', title: a?.source === 'live' ? '来自 DSH 的实时运行状态' : '由事件库推断：还没有收到 DSH 的运行事件，不是实时状态' },
        h('i'),
        h('b', null, a?.label ?? '未知'),
        a?.source !== 'live' && h('small', null, '（推断）'),
      ),
      inc && h('dl', { class: 'rm-kv' }, h('dt', null, '当前事件'), h('dd', { class: 'rm-mono', title: `更新于 ${clock(inc.updatedAt)}` }, inc.id)),
    )
    const ap = inc?.pendingApprovals ?? []
    const approvals =
      ap.length > 0 &&
      section(
        '待确认指令',
        h(
            'ul',
            { class: 'rm-approvals' },
            ...ap.map((p) => {
              const mins = Math.max(0, Math.round((Date.now() - Date.parse(p.askedWall)) / 60000))
              return h(
                'li',
                { 'data-esc': p.escalated ? '1' : '0' },
                h('b', null, p.name),
                h('span', null, `确认人：${p.confirmer ?? '—'}`),
                h('span', null, `已等待 ${mins} 分钟${p.timeoutMin ? `（批准超时 ${p.timeoutMin} 分钟）` : ''}`),
                p.escalated && h('em', null, '已超时升级'),
              )
            }),
            h('p', { class: 'rm-note' }, '请在屏幕下方的确认框里确认或拒绝。'),
          ),
      )
    const rec = section(
      '最近记录',
      s && s.records.length
        ? h(
            'ul',
            { class: 'rm-list rm-records' },
            ...s.records.slice(0, MAX_RECORDS).map((r) => {
              const li = h('li', { role: 'button', tabindex: '0', title: `${r.id}（点击查看）` }, h('span', { class: 'rm-kind' }, r.kindName), h('code', null, r.title || r.id), h('time', null, hm(r.asOf)))
              li.addEventListener('click', () => void openRecord(r.id))
              li.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  void openRecord(r.id)
                }
              })
              return li
            }),
          )
        : empty('暂无记录'),
    )
    rec.classList.add('rm-sec-records')
    right.replaceChildren(...[status, approvals, rec].filter((x): x is HTMLElement => !!x))
  }

  function renderChips(s: StateData | null, staleS: number, failed: boolean): void {
    const out: HTMLElement[] = []
    // 预警等级放在顶栏：全屏地图（左栏收起）时也看得到
    const inc = s?.incident ?? null
    if (inc) {
      const lv = inc.level ? (LEVEL_COLOR[inc.level] ?? '#7fb2e6') : '#7f93ab'
      const name = inc.level === 'none' ? '无预警' : (inc.levelName ?? '未研判')
      out.push(h('span', { class: 'rm-chip rm-chip-level', style: `--lv:${lv}`, title: `${inc.panel ?? ''} ${inc.stageName ?? ''}`.trim() }, h('i'), name))
    }
    // 待人工确认的指令、等操作员回答的卡片：顶栏闪烁提醒。待确认数取自事件库；待回答看页面上有没有 DSH 的提问卡片
    // （智能体等回答时运行状态是“工作中 · 向操作员提问”，不一定是 waiting_question，2026-11-18 演示里看到）
    const nApprovals = inc?.pendingApprovals.length ?? 0
    const asking = s?.agent.state === 'waiting_question' || !!document.querySelector('[data-question-key]')
    if (nApprovals > 0 || asking) {
      const text = [nApprovals > 0 && `${nApprovals} 条待确认`, asking && '待回答'].filter(Boolean).join(' · ')
      const b = h('button', { type: 'button', class: 'rm-chip rm-chip-pending', title: '确认框在屏幕下方；点这里展开两侧面板' }, h('i'), text)
      b.addEventListener('click', onPending)
      out.push(b)
    }
    if (failed || !s) out.push(chip(`状态接口中断${staleS > 0 ? `（${staleS}s）` : ''}`, 'bad'))
    else {
      out.push(chip(s.mine.ok ? '矿井数据' : `矿井数据${s.mine.error ? '异常' : '未配置'}`, s.mine.ok ? 'ok' : 'bad'))
      const sn = s.sentinel
      out.push(!sn.known ? chip('监听器未运行', 'dim') : sn.error ? chip('监听器异常', 'bad') : (sn.ageS ?? 0) > 60 ? chip(`监听中断 ${sn.ageS}s`, 'bad') : chip('监听中', 'ok'))
    }
    chips.replaceChildren(...out)
  }

  return {
    update(s, staleS, failed) {
      mineClock.textContent = s?.mine.now ? clock(s.mine.now) : '—'
      renderChips(s, staleS, failed)
      renderLeft(s, failed)
      renderRight(s)
    },
    dispose() {
      window.clearInterval(timer)
      replay?.stop()
      root.replaceChildren()
    },
  }
}

export function toolsSlot(root: HTMLElement): HTMLElement {
  return root.querySelector('.rm-tools') as HTMLElement
}
