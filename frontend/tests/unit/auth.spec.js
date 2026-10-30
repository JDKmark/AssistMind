import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// login API 不参与本组用例（只验证持久化读取），mock 掉避免真实请求
vi.mock('@/api/auth', () => ({ login: vi.fn() }))

import { useAuthStore } from '@/stores/auth'

describe('auth store 持久化容错', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('用户信息 JSON 损坏时回退未登录态并清理脏数据', () => {
    // 截断的 JSON：旧版本结构变更 / 手动篡改 / 存储配额截断都可能造成
    localStorage.setItem('assistmind_token', 'tok-1')
    localStorage.setItem('assistmind_user', '{"username": "u1", "role": ')

    // store 初始化不应抛异常（否则整个应用白屏且无法通过界面登出恢复）
    const auth = useAuthStore()

    expect(auth.user).toBeNull()
    expect(auth.role).toBe('user')
    expect(localStorage.getItem('assistmind_user')).toBeNull()
    // token 保留：交给后端 401 引导重新登录，不在前端凭空清空会话
    expect(auth.token).toBe('tok-1')
  })

  it('正常用户信息正确解析', () => {
    localStorage.setItem('assistmind_token', 'tok-2')
    localStorage.setItem('assistmind_user', JSON.stringify({ username: 'admin', role: 'admin' }))

    const auth = useAuthStore()

    expect(auth.user).toEqual({ username: 'admin', role: 'admin' })
    expect(auth.role).toBe('admin')
    expect(auth.isLoggedIn).toBe(true)
  })

  it('非对象 JSON（合法但不是用户对象）回退 null', () => {
    localStorage.setItem('assistmind_user', '"admin"')

    const auth = useAuthStore()

    expect(auth.user).toBeNull()
    expect(auth.role).toBe('user')
  })
})
