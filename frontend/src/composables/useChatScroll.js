import { ref, watch, nextTick } from 'vue'

// 流式进行中：每 500ms 刷新诊断面板的实时耗时（staff 定位"卡住"环节用）
const LIVE_TIMER_INTERVAL = 500

/**
 * 消息区滚动与实时计时。
 *
 * 从 views/Chat/index.vue 抽出：
 * - 贴底才跟随（P1-4）：流式期间用户上翻阅读时不被强制拽回底部
 * - staff 诊断面板的实时耗时表（keep-alive 切走要停表，切回要手动恢复）
 *
 * @param {object} deps
 * @param {import('vue').Ref<Array>} deps.messages 消息列表（新消息/增量触发跟随）
 * @param {import('vue').Ref<boolean>} deps.streaming 是否在途流
 * @param {import('vue').Ref<boolean>} deps.isStaff 是否 staff（实时计时只对他们可见）
 */
export function useChatScroll({ messages, streaming, isStaff }) {
  const listRef = ref(null)
  const isAtBottom = ref(true)
  let liveTimer = null

  function scrollToBottom() {
    nextTick(() => {
      const el = listRef.value
      if (el) el.scrollTop = el.scrollHeight
    })
  }

  // 贴底才跟随（P1-4）：滚回底部附近（<40px）自动恢复跟随
  function onListScroll() {
    const el = listRef.value
    if (!el) return
    isAtBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }

  watch(
    messages,
    () => {
      if (isAtBottom.value) scrollToBottom()
    },
    { deep: true },
  )

  // 起停实时耗时计时（staff 诊断面板用）。抽成函数是因为 keep-alive 切走时
  // onDeactivated 会停表，而 streaming 值没变、watch 不会重触发，切回来必须
  // 手动恢复，否则诊断耗时停在上一次刷新的值。
  function syncLiveTimer() {
    if (streaming.value && isStaff.value) {
      if (liveTimer) return // 已在计时，避免重复建表
      liveTimer = setInterval(() => {
        const now = Date.now()
        messages.value.forEach((m) => {
          if (m.started && m.status !== 'done' && m.status !== 'error' && m.sentAt) {
            m.elapsedMs = now - m.sentAt
          }
        })
      }, LIVE_TIMER_INTERVAL)
    } else if (liveTimer) {
      clearInterval(liveTimer)
      liveTimer = null
    }
  }

  watch(streaming, syncLiveTimer)

  function stopLiveTimer() {
    if (liveTimer) {
      clearInterval(liveTimer)
      liveTimer = null
    }
  }

  return { listRef, isAtBottom, scrollToBottom, onListScroll, syncLiveTimer, stopLiveTimer }
}
