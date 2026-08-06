import request from './request'

export function login(data) {
  return request.post('/auth/login', null, { params: data })
}

export function getMe() {
  return request.get('/auth/me')
}
