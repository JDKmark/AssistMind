import { ref, onMounted } from 'vue'
import { getDemoAccounts } from '@/api/auth'

/**
 * 演示账号清单。
 *
 * 数据源是后端 `GET /auth/demo-accounts`：只有 DEBUG 模式才下发口令，
 * 生产环境返回空列表，页面据此隐藏「演示账号」区块。
 * 拉取失败（后端不可用 / 未开放）同样按空处理，不影响正常登录。
 */
export function useDemoAccounts() {
  const accounts = ref([])

  async function load() {
    try {
      accounts.value = await getDemoAccounts()
    } catch {
      accounts.value = []
    }
  }

  onMounted(load)

  return { accounts, load }
}
