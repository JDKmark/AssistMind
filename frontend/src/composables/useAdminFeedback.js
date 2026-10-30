import { ref, computed } from 'vue'
import { listFeedback } from '@/api/feedback'
import { cragLabel as cragLabelOf, cragTagType as cragTagTypeOf } from '@/utils/adminLabels'

/**
 * 反馈与追溯：差评列表筛选、会话时间线抽屉（意图 → 检索 → CRAG → 回答 → 证据链）。
 *
 * 从 views/Admin/index.vue 抽出。追溯抽屉的数据就是被点开的那条反馈，
 * 故 currentTrace 与反馈列表同属一个模块，避免「列表在这、详情在那」的割裂。
 */
export function useAdminFeedback() {
  const feedbacks = ref([])
  const fbLoading = ref(false)
  const fbError = ref('')
  const fbScoreFilter = ref(0) // 0=全部
  const fbExportedFilter = ref('all') // all/exported/pending
  const traceDrawer = ref(false)
  const currentTrace = ref(null)
  const langfuseHost = ref('')

  const cragLabel = computed(() =>
    cragLabelOf(currentTrace.value && currentTrace.value.crag_action),
  )
  const cragTagType = computed(() =>
    cragTagTypeOf(currentTrace.value && currentTrace.value.crag_action),
  )

  function langfuseUrl(traceId) {
    return `${String(langfuseHost.value).replace(/\/+$/, '')}/trace/${traceId}`
  }

  async function loadFeedback() {
    fbLoading.value = true
    fbError.value = ''
    try {
      const params = {}
      if (fbScoreFilter.value) params.score = fbScoreFilter.value
      if (fbExportedFilter.value !== 'all') {
        params.exported = fbExportedFilter.value === 'exported'
      }
      const data = await listFeedback(params)
      feedbacks.value = data.items || []
      langfuseHost.value = data.langfuse_host || ''
    } catch {
      // 错误已由 request 拦截器统一提示
      fbError.value = '反馈数据加载失败'
    } finally {
      fbLoading.value = false
    }
  }

  function openTrace(row) {
    currentTrace.value = row
    traceDrawer.value = true
  }

  return {
    feedbacks,
    fbLoading,
    fbError,
    fbScoreFilter,
    fbExportedFilter,
    traceDrawer,
    currentTrace,
    langfuseHost,
    cragLabel,
    cragTagType,
    langfuseUrl,
    loadFeedback,
    openTrace,
  }
}
