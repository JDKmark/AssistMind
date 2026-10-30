// 管理后台专用文案映射与格式化：工单状态分布、追溯时间线的意图/CRAG 决策、分数。
// 与用户侧 @/utils/labels.js 分开：后台的工单状态多一个 other 兜底桶，
// 追溯文案是后台独有概念，混入公共 labels 会污染用户页面的口径。

export const TICKET_LABELS = {
  open: '待处理',
  in_progress: '处理中',
  resolved: '已解决',
  closed: '已关闭',
  other: '其他',
}

export const INTENT_LABELS = {
  faq: '知识问答',
  task: '工具操作',
  chat: '自由对话',
  unclear: '待澄清',
}

export const CRAG_LABELS = {
  generate: '直接生成',
  rewrite_retry: '被动改写重试',
  no_result: '未找到（空检索短路）',
}

export function statusLabel(s) {
  return TICKET_LABELS[s] || s
}

export function intentLabel(intent) {
  return INTENT_LABELS[intent] || intent || '-'
}

export function cragLabel(action) {
  return CRAG_LABELS[action] || action || '-'
}

export function cragTagType(action) {
  if (action === 'no_result') return 'warning'
  if (action === 'rewrite_retry') return 'warning'
  return 'success'
}

export function fmtScore(score) {
  const n = Number(score)
  return Number.isFinite(n) ? n.toFixed(2) : ''
}
