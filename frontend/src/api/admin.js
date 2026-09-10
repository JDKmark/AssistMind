import request from './request'

export function getOverview() {
  return request.get('/admin/overview')
}

export function listUsers(params = {}) {
  return request.get('/admin/users', { params })
}

export function updateUser(userId, data) {
  return request.patch(`/admin/users/${userId}`, data)
}

export function listAuditLogs(params = {}) {
  return request.get('/admin/audit', { params })
}
