import request from './request'

export function login(data) {
  // 后端 LoginRequest 要求 JSON body（非 query params）
  return request.post('/auth/login', data)
}

export function getMe() {
  return request.get('/auth/me')
}

// 演示账号：仅后端 DEBUG 模式返回数据，生产环境返回空列表
export function getDemoAccounts() {
  return request.get('/auth/demo-accounts')
}
