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
