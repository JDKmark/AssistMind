import { ref } from 'vue'
import { chatStream } from '@/api/chat'
import { stopSpeaking } from '@/utils/speech'

// 客户端历史上限：后端 MEMORY_WINDOW 默认 10 条（5 轮），前端留 4 倍余量。
// 目的仅是限制长会话的请求体大小——服务端 trim_history 会再按窗口裁剪，
// 留余量是为了后端调大窗口时前端不先把有效上下文截掉。
const CLIENT_HISTORY_MAX = 40

/**
 * 一轮问答的发送链路：SSE 订阅、停止生成、客户端 history 维护。
 *
 * 从 views/Chat/index.vue 抽出。history 由本模块持有（发送时切片、done 后追加、
 * 超限裁剪），页面只负责把它的 ref 交给历史会话模块复用。
 *
 * @param {object} deps
 * @param {import('vue').Ref<Array>} deps.messages 消息列表
 * @param {import('vue').Ref<boolean>} deps.streaming 是否在途流
 * @param {import('vue').Ref<string>} deps.inputText 输入框内容
 * @param {import('vue').Ref<string>} deps.currentConversationId 当前会话 ID
 * @param {object} deps.personaStore 人格 store（发送时取 personaId）
 * @param {import('vue').Ref<boolean>} deps.isAtBottom 是否贴底（发送时强制回底）
 * @param {() => void} deps.scrollToBottom 消息区滚底
 * @param {Function} deps.newMessage 消息工厂（useChatMessages）
 * @param {Function} deps.handleEvent SSE 事件落账（useChatMessages）
 * @param {Function} deps.handleError 错误收尾（useChatMessages）
 */
export function useChatStream({
  messages,
  streaming,
  inputText,
  currentConversationId,
  personaStore,
  isAtBottom,
  scrollToBottom,
  newMessage,
  handleEvent,
  handleError,
}) {
  // 已完成轮次的对话历史 [{role: 'user'|'assistant', content}]，随请求发给后端
  const history = ref([])
  // 当前在途流的 AbortController + 是否由用户手动停止（停止不是错误，气泡正常收尾）
  let activeAbort = null
  let stoppedByUser = false

  function trimClientHistory() {
    if (history.value.length > CLIENT_HISTORY_MAX) {
      history.value = history.value.slice(-CLIENT_HISTORY_MAX)
    }
  }

  // 停止生成：中止在途 SSE 流，保留已输出内容（管理员/用户不想等长回答时用）
  function stopStream() {
    if (!streaming.value) return
    stopSpeaking()
    stoppedByUser = true
    activeAbort?.abort()
  }

  // 组件真正销毁时终止在途流（登出后布局卸载等场景）
  function abortActive() {
    activeAbort?.abort()
  }

  async function onSend() {
    const q = inputText.value.trim()
    if (!q || streaming.value) return
    // 新提问到达：打断仍在进行的上一轮播报
    stopSpeaking()
    const assistantMsg = newMessage('assistant', '')
    assistantMsg.sentAt = Date.now()
    messages.value.push(newMessage('user', q), assistantMsg)
    // 关键：后续 SSE 事件写入必须走「响应式代理版」消息对象。
    // newMessage() 返回原始对象，push 后 Vue 已将其转为 reactive proxy——
    // 继续用 raw 对象写入不会触发更新（tool_result 到达后卡片依赖 done 兜底渲染，
    // 行为脆弱且不符合直觉）。代理引用保证每条事件写入即时派发重渲染。
    const liveMsg = messages.value[messages.value.length - 1]
    inputText.value = ''
    streaming.value = true
    stoppedByUser = false
    activeAbort = new AbortController()
    // 用户主动发言：无条件回底（并恢复贴底跟随）
    isAtBottom.value = true
    scrollToBottom()
    try {
      await chatStream(q, {
        history: history.value.slice(),
        persona: personaStore.personaId || null,
        // 继续历史会话时非空：该轮消息归属原会话；新会话为空，由 start 事件采纳
        conversationId: currentConversationId.value,
        // 停止生成：中止信号（fetch 收到 AbortError），已生成内容保留
        signal: activeAbort.signal,
        onEvent: (name, data) => {
          handleEvent(liveMsg, name, data)
          if (name === 'done') {
            history.value.push({ role: 'user', content: q })
            history.value.push({ role: 'assistant', content: liveMsg.content || '' })
            trimClientHistory()
          }
        },
        onDone: () => {
          streaming.value = false
          activeAbort = null
        },
        onError: (message) => {
          streaming.value = false
          activeAbort = null
          if (stoppedByUser) {
            // 用户手动停止：保留已生成内容并正常收尾，不显示错误
            liveMsg.status = 'done'
            liveMsg.stage = 'done'
            return
          }
          handleError(liveMsg, message)
        },
      })
    } catch (e) {
      // chatStream 内部已上报错误（reported 守卫），此处仅兜底清理状态
      streaming.value = false
      activeAbort = null
      if (stoppedByUser) {
        liveMsg.status = 'done'
        liveMsg.stage = 'done'
        return
      }
      handleError(liveMsg, (e && e.message) || '网络请求失败')
    }
  }

  return { history, trimClientHistory, onSend, stopStream, abortActive }
}
