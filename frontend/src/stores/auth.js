import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { login as loginApi } from '@/api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('assistmind_token') || '')
  const user = ref(JSON.parse(localStorage.getItem('assistmind_user') || 'null'))

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
