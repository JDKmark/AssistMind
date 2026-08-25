import request from './request'

export function listOrders(params = {}) {
  return request.get('/mall/orders', { params })
}

export function listMyOrders(params = {}) {
  return request.get('/mall/my-orders', { params })
}

export function listRefunds(params = {}) {
  return request.get('/mall/refunds', { params })
}

export function updateRefundStatus(refundId, status) {
  return request.patch(`/mall/refunds/${refundId}/status`, { status })
}
