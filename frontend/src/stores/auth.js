import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { login as loginApi } from '@/api/auth'

/**
 * 读取持久化的用户信息。
 *
 * 解析失败必须就地兜底：store 定义期抛异常会让整个应用白屏，且用户无法
 * 通过界面登出恢复（只能手动清 localStorage）。损坏来源包括旧版本 user
 * 结构变更、手动篡改、存储配额截断。
 */
function readStoredUser() {
  const raw = localStorage.getItem('assistmind_user')
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    // 降级：丢弃损坏数据，按未登录处理（token 仍在时由后端 401 引导重新登录）
    localStorage.removeItem('assistmind_user')
    return null
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('assistmind_token') || '')
  const user = ref(readStoredUser())

  const isLoggedIn = computed(() => !!token.value)
  const role = computed(() => user.value?.role || 'user')

  async function login(username, password) {
    const data = await loginApi({ username, password })
    token.value = data.access_token
    user.value = data.user
    localStorage.setItem('assistmind_token', data.access_token)
    localStorage.setItem('assistmind_user', JSON.stringify(data.user))
    return data
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('assistmind_token')
    localStorage.removeItem('assistmind_user')
  }

  return { token, user, isLoggedIn, role, login, logout }
})
