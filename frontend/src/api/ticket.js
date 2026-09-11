import request from './request'

export function listTickets(status, limit, filters = {}) {
  const params = { ...filters }
  if (status) params.status = status
  if (limit) params.limit = limit
  return request.get('/ticket/list', { params })
}

export function getTicket(ticketId) {
  return request.get(`/ticket/${ticketId}`)
}

export function createTicket(data) {
  return request.post('/ticket/', data)
}

export function updateTicketStatus(ticketId, newStatus) {
  return request.patch(`/ticket/${ticketId}/status`, { status: newStatus })
}

export function listReplies(ticketId) {
  return request.get(`/ticket/${ticketId}/replies`)
}

export function addReply(ticketId, content) {
  return request.post(`/ticket/${ticketId}/replies`, { content })
}

// 工单更新轮询：silent=true 走 request.js 静默通道（失败不弹窗、不置后端不可用状态），
// 由 ticketPolling 捕获后 console.warn 静默降级
export function getTicketUpdates(sinceIso) {
  const params = {}
  if (sinceIso) params.since = sinceIso
  return request.get('/ticket/updates', { params, silent: true })
}
