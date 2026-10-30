import { ref } from 'vue'
import { listReplies, addReply } from '@/api/ticket'

// 轮询定时器（15s 一轮）；页面卸载时清理，避免残留请求
const THREAD_POLL_INTERVAL = 15000

/**
 * 人工客服线程（transfer_human 成功后挂接的工单回复通道）。
 *
 * 从 views/Chat/index.vue 抽出。这里只管数据：轮询拉取、按 id 去重合并、用户回帖。
 * 消息区自动滚底属于展示细节，由 HumanThreadPanel 监听 replies 长度自行处理。
 */
export function useHumanThread() {
  const humanThreadTicketId = ref('')
  // 线程数据：{ ticketId, replies }；staff 与用户回复统一按 reply.id 去重合并
  const humanThread = ref({ ticketId: '', replies: [] })
  const threadDraft = ref('')
  const threadSending = ref(false)

  let threadTimer = null

  // 按 reply.id 去重合并回复（轮询与本地发送共用）
  function mergeReplies(list) {
    const existing = new Set(humanThread.value.replies.map((r) => r.id))
    for (const r of list || []) {
      if (r && r.id != null && !existing.has(r.id)) {
        humanThread.value.replies.push(r)
        existing.add(r.id)
      }
    }
  }

  async function loadHumanReplies() {
    if (!humanThreadTicketId.value) return
    try {
      const data = await listReplies(humanThreadTicketId.value)
      mergeReplies(data.replies || [])
    } catch (e) {
      // 错误提示已由 request 拦截器统一处理；单轮失败不中断，下个周期重试
      console.warn('[Chat] 人工客服回复轮询失败', e)
    }
  }

  // transfer_human 成功：挂接该工单回复线程（立即拉一次 + 15s 轮询，卸载停止）
  function startHumanThread(ticketId) {
    if (humanThreadTicketId.value === ticketId) return
    humanThreadTicketId.value = ticketId
    humanThread.value = { ticketId, replies: [] }
    if (threadTimer) clearInterval(threadTimer)
    threadTimer = setInterval(loadHumanReplies, THREAD_POLL_INTERVAL)
    loadHumanReplies()
  }

  // 用户在线程内回帖：写入工单 replies（sender_role=user 由后端 JWT 定），成功后即时渲染
  async function sendThreadReply() {
    const content = threadDraft.value.trim()
    if (!content || threadSending.value || !humanThreadTicketId.value) return
    threadSending.value = true
    try {
      const reply = await addReply(humanThreadTicketId.value, content)
      threadDraft.value = ''
      if (reply && reply.id != null) {
        mergeReplies([reply])
      } else {
        // 返回体异常时兜底走一次轮询对齐服务端状态
        await loadHumanReplies()
      }
    } catch (e) {
      // 失败提示由 request 拦截器统一处理；输入保留便于重发
      console.warn('[Chat] 人工客服回复发送失败', e)
    } finally {
      threadSending.value = false
    }
  }

  // 页面卸载：停止轮询（无残留定时器）
  function stopThreadPolling() {
    if (threadTimer) {
      clearInterval(threadTimer)
      threadTimer = null
    }
  }

  return {
    humanThreadTicketId,
    humanThread,
    threadDraft,
    threadSending,
    startHumanThread,
    loadHumanReplies,
    sendThreadReply,
    stopThreadPolling,
  }
}
