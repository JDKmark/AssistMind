import axios from 'axios'
import { ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { ElMessage } from 'element-plus'

// 后端可用性状态：任一请求网络失败/5xx 置 down（页面可据此展示降级横幅），
// 任一请求成功即恢复。
export const backendStatus = ref({ down: false })

// 错误提示全局限流：页面加载时常并发多个请求（如管理后台 10 个），
// 后端不可用时会同时失败——错误提示 3 秒内只弹一条，同文案自动合并，
// 避免弹窗轰炸让用户误以为页面异常/有害网站。
let lastErrorToastAt = 0
const ERROR_TOAST_MIN_INTERVAL = 3000

function showErrorToast(msg, minInterval = ERROR_TOAST_MIN_INTERVAL) {
  const now = Date.now()
  if (now - lastErrorToastAt < minInterval) return
  lastErrorToastAt = now
  ElMessage.error({ message: msg, grouping: true })
}

const request = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

// 请求拦截：携带 JWT
request.interceptors.request.use(
  (config) => {
    const auth = useAuthStore()
    if (auth.token) {
      config.headers.Authorization = `Bearer ${auth.token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// 响应拦截：统一错误处理
request.interceptors.response.use(
  (response) => {
    backendStatus.value.down = false
    return response.data
  },
  (error) => {
    // 静默通道（config.silent=true，如工单徽标轮询）：失败不弹窗、不影响全局
    // 后端状态，由调用方自行 console.warn 降级（下轮轮询自动重试）
    if (error.config?.silent) {
      return Promise.reject(error)
    }
    const status = error.response?.status
    // 网络失败（无 response，如 vite 代理 502/后端宕机）或 5xx：后端不可用
    if (!status || status >= 500) {
      backendStatus.value.down = true
      // 10 秒节流：后端宕机期间反复操作也不会连环弹窗
      showErrorToast('后端服务暂不可用，请稍后重试', 10000)
      return Promise.reject(error)
    }
    const msg = error.response?.data?.detail || error.message || '请求失败'
    if (status === 401) {
      const auth = useAuthStore()
      auth.logout()
      window.location.href = '/login'
    } else {
      showErrorToast(msg)
    }
    return Promise.reject(error)
  }
)

export default request
