import { ref, computed } from 'vue'
import { getHealth } from '@/api/health'

const DEP_LABELS = {
  qdrant: 'Qdrant',
  redis: 'Redis',
  postgres: 'PostgreSQL',
  langfuse: 'Langfuse',
}
const STATUS_TEXTS = { ok: '正常', disabled: '未启用', pending: '待检测', error: '异常' }
const STATUS_TAG_TYPES = { ok: 'success', disabled: 'info', pending: 'warning', error: 'danger' }

/**
 * 系统状态面板：健康检查 + 依赖状态映射 + 整体结论。
 *
 * 从 views/Admin/index.vue 抽出。依赖固定四件套（Qdrant/Redis/PostgreSQL/Langfuse），
 * 顺序与文案即面板渲染顺序，故映射表放在本模块内。
 */
export function useAdminHealth() {
  const health = ref(null)
  const healthLoading = ref(false)

  const depRows = computed(() => {
    const deps = (health.value && health.value.dependencies) || {}
    return Object.keys(DEP_LABELS).map((key) => {
      const st = deps[key] || 'pending'
      return {
        key,
        label: DEP_LABELS[key],
        status: st,
        statusText: STATUS_TEXTS[st] || st,
        tagType: STATUS_TAG_TYPES[st] || 'info',
      }
    })
  })

  const depStatuses = computed(() => depRows.value.map((r) => r.status))

  const overallLabel = computed(() => {
    if (depStatuses.value.includes('error')) return '异常'
    if (depStatuses.value.includes('pending')) return '部分待定'
    return '正常'
  })

  const overallTagType = computed(() => {
    if (depStatuses.value.includes('error')) return 'danger'
    if (depStatuses.value.includes('pending')) return 'warning'
    return 'success'
  })

  async function loadHealth() {
    healthLoading.value = true
    try {
      health.value = await getHealth()
    } catch {
      // 错误已由 request 拦截器统一提示
    } finally {
      healthLoading.value = false
    }
  }

  return { health, healthLoading, depRows, depStatuses, overallLabel, overallTagType, loadHealth }
}
