import { ref } from 'vue'
import { getOverview } from '@/api/admin'
import { listDocs } from '@/api/knowledge'
import { listTickets } from '@/api/ticket'

/**
 * 数据概览卡：知识库统计（文档数 / chunk 数）+ 全量工单快照。
 *
 * 从 views/Admin/index.vue 抽出。概览与工单面板共用同一份工单快照，
 * 因此这里不自己持有 tickets，而是通过 applyTickets 交回 useAdminTickets，
 * 避免两处各存一份列表导致状态分叉。
 *
 * @param {object} deps
 * @param {(list: Array, total: number) => void} deps.applyTickets 工单快照写回
 */
export function useAdminOverview({ applyTickets }) {
  const adminOverview = ref({
    orders: { total: 0, amount: 0, by_status: {} },
    tickets: { total: 0, by_status: {} },
    refunds: { total: 0, by_status: {} },
    users: { total: 0, by_role: {} },
    feedback: { total: 0, negative: 0, pending_export: 0 },
    knowledge: { documents: 0, chunks: 0 },
    degraded: [],
  })
  const kbTotal = ref(0)
  const kbChunks = ref(0)
  const kbError = ref('')
  const dataLoading = ref(false)

  async function loadOverview() {
    dataLoading.value = true
    try {
      const [overview, kb, ticketData] = await Promise.all([
        getOverview(),
        listDocs(),
        listTickets(null, 200),
      ])
      adminOverview.value = overview
      kbTotal.value = kb.total || 0
      kbChunks.value = (kb.docs || []).reduce((sum, d) => sum + (d.chunk_count || 0), 0)
      kbError.value = kb.error || ''
      applyTickets(ticketData.tickets, ticketData.total)
    } catch {
      kbError.value = '管理概览加载失败'
    } finally {
      dataLoading.value = false
    }
  }

  return { adminOverview, kbTotal, kbChunks, kbError, dataLoading, loadOverview }
}
