import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { listTickets, getTicket, listReplies, addReply } from '@/api/ticket'

/**
 * 工单面板：筛选查询、状态分布、详情抽屉与人工回复。
 *
 * 从 views/Admin/index.vue 抽出。tickets 是全页面唯一的工单快照
 * （概览卡的「工单总数/状态分布」也读它），所以快照写回统一走 applyTickets，
 * 由 useAdminOverview 在加载概览时复用，避免两处列表分叉。
 */
export function useAdminTickets() {
  const tickets = ref([])
  const ticketTotal = ref(0)
  const ticketStatusFilter = ref('')
  const ticketPriorityFilter = ref('')
  const ticketCustomerFilter = ref('')

  // ---------- 工单详情（管理员查看） ----------
  const ticketDetailVisible = ref(false)
  const ticketDetailLoading = ref(false)
  const currentTicketDetail = ref(null)
  const ticketReplies = ref([])
  const ticketReplyDraft = ref('')
  const replySubmitting = ref(false)

  const statusDist = computed(() => {
    const dist = { open: 0, in_progress: 0, resolved: 0, closed: 0, other: 0 }
    for (const t of tickets.value) {
      const st = t.status || 'other'
      if (st in dist) {
        dist[st] += 1
      } else {
        dist.other += 1
      }
    }
    return dist
  })

  // 工单快照写回（概览加载与筛选查询共用同一口径）
  function applyTickets(list, total) {
    tickets.value = list || []
    ticketTotal.value = total != null ? total : tickets.value.length
  }

  async function searchTickets() {
    try {
      const filters = {}
      if (ticketPriorityFilter.value) filters.priority = ticketPriorityFilter.value
      if (ticketCustomerFilter.value.trim()) filters.user_id = ticketCustomerFilter.value.trim()
      const data = await listTickets(ticketStatusFilter.value || null, 50, filters)
      applyTickets(data.tickets, data.total)
    } catch (e) {
      console.warn('[Admin] 工单查询失败', e)
      tickets.value = []
      ticketTotal.value = 0
    }
  }

  async function openTicketDetail(row) {
    ticketDetailVisible.value = true
    ticketDetailLoading.value = true
    currentTicketDetail.value = null
    ticketReplies.value = []
    ticketReplyDraft.value = ''
    try {
      const [detail, replyData] = await Promise.all([getTicket(row.id), listReplies(row.id)])
      currentTicketDetail.value = detail
      ticketReplies.value = replyData.replies || []
    } catch {
      // 错误已由 request 拦截器统一提示
      currentTicketDetail.value = null
    } finally {
      ticketDetailLoading.value = false
    }
  }

  async function sendTicketReply() {
    const content = ticketReplyDraft.value.trim()
    if (!content || !currentTicketDetail.value) return
    replySubmitting.value = true
    try {
      await addReply(currentTicketDetail.value.id, content)
      ElMessage.success('回复已发送，用户可在工单详情查看')
      ticketReplyDraft.value = ''
      const replyData = await listReplies(currentTicketDetail.value.id)
      ticketReplies.value = replyData.replies || []
      // 同步弹窗内状态显示（客服回复 open 工单后端已自动转 in_progress）
      if (currentTicketDetail.value.status === 'open') {
        currentTicketDetail.value.status = 'in_progress'
      }
      await searchTickets()
    } catch (e) {
      console.warn('[Admin] 回复发送失败', e)
    } finally {
      replySubmitting.value = false
    }
  }

  return {
    tickets,
    ticketTotal,
    ticketStatusFilter,
    ticketPriorityFilter,
    ticketCustomerFilter,
    statusDist,
    applyTickets,
    searchTickets,
    ticketDetailVisible,
    ticketDetailLoading,
    currentTicketDetail,
    ticketReplies,
    ticketReplyDraft,
    replySubmitting,
    openTicketDetail,
    sendTicketReply,
  }
}
