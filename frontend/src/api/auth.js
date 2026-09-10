import request from './request'

export function login(data) {
  // 后端 LoginRequest 要求 JSON body（非 query params）
  return request.post('/auth/login', data)
}

export function getMe() {
  return request.get('/auth/me')
}
