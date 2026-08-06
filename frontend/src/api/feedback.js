import request from './request'

export function submitFeedback(data) {
  return request.post('/feedback/', data)
}
