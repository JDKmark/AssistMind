import { ref } from 'vue'
import { listConversations, listMessages } from '@/api/conversation'

/**
 * 历史会话抽屉：列表拉取、查看消息、继续对话、新对话。
 *
 * 从 views/Chat/index.vue 抽出。继续/新对话都会改写主消息区与客户端 history，
 * 因此这些共享 ref 由页面注入，本模块只做「怎么改」。
 *
 * @param {object} deps
 * @param {import('vue').Ref<Array>} deps.messages 主消息列表
 * @param {import('vue').Ref<boolean>} deps.streaming 是否在途流（在途时禁止切换）
 * @param {import('vue').Ref<Array>} deps.history 客户端对话历史（继续对话时重建）
 * @param {() => void} deps.trimClientHistory 历史裁剪
 * @param {import('vue').Ref<string>} deps.currentConversationId 当前会话 ID
 * @param {Function} deps.newMessage 消息工厂（提示消息用）
 * @param {() => void} deps.scrollToBottom 消息区滚底
 */
export function useConversationHistory({
  messages,
  streaming,
  history,
  trimClientHistory,
  currentConversationId,
  newMessage,
  scrollToBottom,
}) {
  const historyDrawerVisible = ref(false)
  const historyLoading = ref(false)
  // 会话列表（本人，updated_at 倒序）
  const conversations = ref([])
  // 「查看」模式：抽屉内下方展开的会话消息（正序）
  const viewedMessages = ref([])

  async function openHistoryDrawer() {
    historyDrawerVisible.value = true
    // 每次打开都拉最新列表（新完成的问答会浮到最上）
    await loadConversations()
  }

  async function loadConversations() {
    historyLoading.value = true
    try {
      const data = await listConversations()
      conversations.value = data.conversations || []
    } catch (e) {
      // 错误提示已由 request 拦截器统一处理；列表置空走抽屉空态
      conversations.value = []
      console.warn('[Chat] 会话列表加载失败', e)
    } finally {
      historyLoading.value = false
    }
  }

  // 「查看」：抽屉内下方展开该会话消息（正序）
  async function viewConversation(c) {
    viewedMessages.value = []
    try {
      const data = await listMessages(c.id)
      viewedMessages.value = data.messages || []
    } catch (e) {
      // 错误提示已由 request 拦截器统一处理；保持空列表
      console.warn('[Chat] 会话消息加载失败', e)
    }
  }

  // 「继续对话」：重建 history、归属原会话，主消息区清空并插入提示
  async function continueConversation(c) {
    // 流式进行中禁止切换：否则旧流的 start 事件会把自己的 conversation_id
    // 误采纳为当前会话，旧 done 还会污染刚清空的 history（并发竞态）
    if (streaming.value) return
    let rebuilt = []
    try {
      const data = await listMessages(c.id)
      rebuilt = (data.messages || [])
        .filter((m) => m && m.content)
        .map((m) => ({ role: m.role, content: m.content }))
    } catch (e) {
      // 拉取失败仍切换会话（后续请求带 conversation_id，服务端保存有消息），
      // history 置空仅影响本地上下文，不阻塞切换
      console.warn('[Chat] 会话消息重建失败，仅切换会话', e)
    }
    history.value = rebuilt
    trimClientHistory()
    currentConversationId.value = c.id
    historyDrawerVisible.value = false
    viewedMessages.value = []
    messages.value = []
    const tip = newMessage('assistant', '已切换到历史会话，可继续提问')
    tip.status = 'done'
    tip.stage = 'done'
    tip.isNotice = true
    messages.value.push(tip)
    scrollToBottom()
  }

  // 「新对话」：清空历史/当前会话/消息列表，回到空态
  function startNewConversation() {
    // 流式进行中禁止清空：currentConversationId 置空会让在途流的 start 事件
    // 被误采纳为新会话 id，旧问答也会写进新会话上下文（并发竞态）
    if (streaming.value) return
    history.value = []
    currentConversationId.value = ''
    viewedMessages.value = []
    messages.value = []
    historyDrawerVisible.value = false
  }

  return {
    historyDrawerVisible,
    historyLoading,
    conversations,
    viewedMessages,
    openHistoryDrawer,
    loadConversations,
    viewConversation,
    continueConversation,
    startNewConversation,
  }
}
