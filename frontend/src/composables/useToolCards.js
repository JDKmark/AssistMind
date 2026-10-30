/**
 * 工具调用展示：工具名中文标签、参数摘要、结果卡片结构化解析、工单号提取。
 *
 * 从 views/Chat/index.vue 抽出。这里全是纯函数（不持有响应式状态），
 * 消息对象由调用方持有；`cardOf` 会在工具调用对象上挂惰性缓存字段。
 */

// 工具名 → 中文标签
export const TOOL_LABELS = {
  search_knowledge: '检索知识库',
  create_ticket: '创建工单',
  transfer_human: '转人工客服',
  get_ticket_status: '查询工单状态',
  apply_refund: '申请退款',
  query_order: '查询订单',
  query_logistics: '查询物流',
  query_product: '查询商品',
}

export function toolLabel(name) {
  return TOOL_LABELS[name] || name
}

export function toolArgsText(tc) {
  if (!tc.arguments) return ''
  let text = ''
  try {
    text = JSON.stringify(tc.arguments)
  } catch {
    text = String(tc.arguments)
  }
  return text.length > 80 ? `${text.slice(0, 80)}…` : text
}

// 工具结果 → 卡片结构化数据（订单/物流/商品/售后）；不可解析返回 null（回退 JSON 文本）
export function parseToolResult(tc) {
  const name = tc.tool_name
  const result = tc.result
  if (result === undefined || result === null) return null
  // MCP 工具返回可能是 {result: ...} 包装，也可能是直接 dict/list
  const data = result && typeof result === 'object' && 'result' in result ? result.result : result

  if (
    name === 'query_order' &&
    data &&
    typeof data === 'object' &&
    !Array.isArray(data) &&
    data.order_sn
  ) {
    return { type: 'order', data }
  }
  if (name === 'query_logistics' && Array.isArray(data) && data.length && data[0].ts) {
    return { type: 'logistics', data }
  }
  if (
    name === 'query_product' &&
    data &&
    typeof data === 'object' &&
    !Array.isArray(data) &&
    data.id
  ) {
    return { type: 'product', data }
  }
  if (
    name === 'apply_refund' &&
    data &&
    typeof data === 'object' &&
    !Array.isArray(data) &&
    data.refund_id
  ) {
    return { type: 'refund', data }
  }
  return null
}

// 惰性缓存解析结果（模板中多次引用只算一次）。
// 关键：SSE 的 tool_call 先到、tool_result 后到——result 从 undefined 变为对象时
// 必须失效重算，否则先渲染出的 _card=null 会永久缓存，卡片永不出现（真实时序 bug）
export function cardOf(tc) {
  if (tc._cardKey !== tc.result) {
    tc._cardKey = tc.result
    tc._card = parseToolResult(tc)
  }
  return tc._card
}

// 从工具结果中提取工单号（MCP create_ticket / transfer_human 返回 {ticket_id}）
export function extractTicketId(result) {
  if (!result) return ''
  if (typeof result === 'object' && result.ticket_id) return result.ticket_id
  if (typeof result === 'string') {
    const m = result.match(/TK-\d{7,}/)
    if (m) return m[0]
  }
  return ''
}

export function useToolCards() {
  return { TOOL_LABELS, toolLabel, toolArgsText, parseToolResult, cardOf, extractTicketId }
}
