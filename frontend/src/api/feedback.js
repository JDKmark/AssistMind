import request from './request'

export function submitFeedback(data) {
  return request.post('/feedback/', data)
}

export function listFeedback(params) {
  return request.get('/feedback/', { params })
}