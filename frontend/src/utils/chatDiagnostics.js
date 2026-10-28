/**
 * 对话诊断的纯函数集合。
 *
 * 从 `views/Chat/index.vue` 抽出：消息诊断面板（阶段耗时 / 事件时间线 / 完整诊断 JSON）
 * 与 SSE 事件记录共用这里的格式化逻辑，避免页面组件与展示组件各写一份。
 * 本模块不依赖 Vue 响应式与 DOM，便于单测。
 */

export const STAGE_TEXT = {
  start: '正在思考…',
  disambiguation: '正在识别产品…',
  retrieving: '正在检索知识库…',
  tool: '正在为您处理，请稍候…',
  generating: '正在生成回答…',
}

const INTENT_LABELS = {
  faq: '知识问答',
  task: '任务处理',
  chat: '闲聊',
  unclear: '意图不明',
}

const ROUTE_LABELS = {
  rule: '规则命中',
  semantic: '语义命中',
  llm: 'LLM 分类',
  fallback: '降级兜底',
}

const CRAG_LABELS = {
  generate: 'CRAG: 直接生成',
  rewrite_retry: 'CRAG: 改写重试',
  no_result: 'CRAG: 无结果',
}

const EVENT_LABELS = {
  start: '开始',
  disambiguation: '消歧',
  retrieving: '检索',
  rewriting: '改写',
  generating: '生成中',
  delta: '流式输出',
  tool_call: '工具调用',
  tool_result: '工具结果',
  done: '完成',
  error: '错误',
}

export function stageText(m) {
  return STAGE_TEXT[m.stage] || '正在思考…'
}

export function intentLabel(intent) {
  return INTENT_LABELS[intent] || intent || '—'
}

export function routeLabel(source) {
  return ROUTE_LABELS[source] || source || ''
}

export function cragLabel(action) {
  return CRAG_LABELS[action] || (action ? action.toUpperCase() : '')
}

export function eventLabel(name) {
  return EVENT_LABELS[name] || name
}

export function fmtMs(ms) {
  const n = Number(ms)
  if (!Number.isFinite(n) || n < 0) return ''
  return n >= 10000 ? `${(n / 1000).toFixed(1)}s` : `${n}ms`
}

// 来源分数展示（保留 2 位小数）
export function fmtScore(score) {
  const n = Number(score)
  return Number.isFinite(n) ? n.toFixed(2) : ''
}

export function summarize(v, max = 200) {
  if (v === undefined || v === null) return v
  let s
  try {
    s = typeof v === 'string' ? v : JSON.stringify(v)
  } catch {
    s = String(v)
  }
  return s.length > max ? `${s.slice(0, max)}…` : s
}

// 事件数据快照（截断大对象，避免诊断面板堆积噪声）
export function eventSnap(name, data) {
  const d = data || {}
  switch (name) {
    case 'tool_result':
      return { tool_name: d.tool_name, result: summarize(d.result, 160) }
    case 'tool_call':
      return { tool_name: d.tool_name, arguments: summarize(d.arguments, 120) }
    case 'start':
      return {
        intent: d.intent,
        route_source: d.route_source,
        role: d.role,
        conversation_id: d.conversation_id,
      }
    case 'rewriting':
      return { variants: Array.isArray(d.variants) ? d.variants : [] }
    case 'done':
      return {
        from_cache: d.from_cache,
        crag_action: d.crag_action,
        crag_score: d.crag_score,
        degraded: d.degraded,
        timings: d.timings,
      }
    default:
      return d
  }
}

// 诊断时间线：事件快照按时刻正序（delta 摘要插到实际发生位置），间隔重算
export function displayEvents(m) {
  const evs = m.eventLog.map((e) => ({ ...e }))
  if (m.deltaCount) {
    evs.push({
      name: 'delta',
      t: m.deltaLastMs,
      dt: 0,
      data: { count: m.deltaCount, chars: m.deltaChars, firstMs: m.deltaFirstMs },
    })
    evs.sort((a, b) => a.t - b.t)
  }
  evs.forEach((e, i) => {
    e.dt = i > 0 ? e.t - evs[i - 1].t : 0
  })
  return evs
}

export function evtDataText(e) {
  const d = e.data || {}
  switch (e.name) {
    case 'start':
      return (
        `意图: ${intentLabel(d.intent)}` +
        (d.route_source ? ` · ${routeLabel(d.route_source)}` : '') +
        (d.role ? ` · role=${d.role}` : '')
      )
    case 'tool_call':
      return `tool: ${d.tool_name || ''} args=${summarize(d.arguments, 60)}`
    case 'tool_result':
      return `${d.tool_name || ''} => ${summarize(d.result, 60)}`
    case 'rewriting':
      return `变体×${(d.variants || []).length}`
    case 'delta':
      return `×${d.count} 块 / ${d.chars} 字符（首块 ${fmtMs(d.firstMs)}）`
    case 'done':
      return (
        `cache: ${d.from_cache || '—'} · crag: ${d.crag_action || '—'} · ` +
        (Array.isArray(d.degraded) && d.degraded.length
          ? `降级: ${d.degraded.join('、')}`
          : '无降级')
      )
    case 'error':
      return `msg: ${d.message || ''}`
    default:
      return ''
  }
}

// 后端 faq 阶段计时（毫秒）存在时优先展示（网络层耗时不可靠）
export function hasBackendTimings(m) {
  const o = m.timings || {}
  return o.total_ms != null || o.retrieve_ms != null || o.generate_ms != null
}

// 阶段耗时（ms）：后端 timings 优先，否则由事件相对时刻差推断（faq 链路为主）
export function stageBreakdown(m) {
  const o = m.timings || {}
  if (hasBackendTimings(m)) {
    const rows = []
    if (o.retrieve_ms != null) rows.push(['检索', o.retrieve_ms])
    if (o.generate_ms != null) rows.push(['生成', o.generate_ms])
    if (o.total_ms != null) rows.push(['后端总耗时', o.total_ms])
    return rows
  }
  const st = m.stageTs
  const chain = [
    ['start', '路由'],
    ['retrieving', '检索'],
    ['rewriting', '改写'],
    ['generating', '生成'],
    ['done', '完成'],
  ]
  const rows = []
  for (let i = 1; i < chain.length; i += 1) {
    const cur = st[chain[i][0]]
    const prev = st[chain[i - 1][0]]
    if (cur != null && prev != null) rows.push([chain[i][1], cur - prev])
  }
  if (m.elapsedMs) rows.push(['前端总耗时', m.elapsedMs])
  return rows
}

/**
 * 完整诊断 JSON（admin/agent 一键复制上报排查）。
 *
 * @param {object} m 助手消息
 * @param {{query?: string, username?: string, role?: string, personaId?: string}} ctx 上下文
 */
export function buildDiagBlob(m, ctx = {}) {
  return JSON.stringify(
    {
      user: ctx.username || '',
      role: ctx.role || '',
      ask_time: m.sentAt ? new Date(m.sentAt).toISOString() : '',
      total_ms: m.elapsedMs || '',
      intent: m.intent || '',
      route_source: m.routeSource || '',
      persona: ctx.personaId || '',
      from_cache: m.fromCache || '',
      crag_action: m.cragAction || '',
      crag_score: m.cragScore || '',
      degraded: m.degraded || [],
      rewrites: m.rewrites || [],
      backend_timings_ms: m.timings || {},
      query: ctx.query || '',
      answer: m.content || '',
      sources: m.sources || [],
      tools: (m.toolCalls || []).map((tc) => ({
        name: tc.tool_name,
        args: summarize(tc.arguments, 400),
        result: summarize(tc.result, 800),
      })),
      events: displayEvents(m).map((e) => ({
        name: e.name,
        t_ms: e.t,
        gap_ms: e.dt,
        data: e.data,
      })),
      error: m.errorMsg || '',
      trace_id: m.traceId || '',
      conversation_id: m.conversationId || '',
    },
    null,
    2,
  )
}

/** 复制文本到剪贴板（不可用时静默降级） */
export function copyText(text) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).catch(() => {})
  }
}
