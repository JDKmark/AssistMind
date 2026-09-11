// 状态/角色中文文案映射：管理后台与用户页面共用，避免英文原码直出
// （工单状态/优先级与 Tickets 页既有文案保持一致）

export const TICKET_STATUS_LABELS = {
  open: '待处理',
  in_progress: '处理中',
  resolved: '已解决',
  closed: '已关闭',
}

export const TICKET_STATUS_TAG_TYPES = {
  open: 'info',
  in_progress: 'warning',
  resolved: 'success',
  closed: '',
}

export const PRIORITY_LABELS = {
  low: '低',
  normal: '普通',
  high: '高',
  medium: '中',
  urgent: '紧急',
}

export const PRIORITY_TAG_TYPES = {
  low: 'info',
  normal: '',
  medium: 'warning',
  high: 'warning',
  urgent: 'danger',
}

export const ROLE_LABELS = {
  admin: '管理员',
  agent: '客服',
  user: '用户',
}

export const ROLE_TAG_TYPES = {
  admin: 'danger',
  agent: 'warning',
  user: 'info',
}

export function ticketStatusLabel(s) {
  return TICKET_STATUS_LABELS[s] || s || '-'
}

export function ticketStatusTagType(s) {
  return TICKET_STATUS_TAG_TYPES[s] || 'info'
}

export function priorityLabel(p) {
  return PRIORITY_LABELS[p] || p || '-'
}

export function priorityTagType(p) {
  return PRIORITY_TAG_TYPES[p] || 'info'
}

export function roleLabel(r) {
  return ROLE_LABELS[r] || r || '-'
}

export function roleTagType(r) {
  return ROLE_TAG_TYPES[r] || 'info'
}
