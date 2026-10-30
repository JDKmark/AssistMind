import { ElMessage } from 'element-plus'
import { submitFeedback } from '@/api/feedback'
import { eventSnap } from '@/utils/chatDiagnostics'
import { speak } from '@/utils/speech'
import { extractTicketId } from './useToolCards'

/**
 * 会话消息：消息工厂 + SSE 事件落账 + 错误收尾 + 单条满意度反馈。
 *
 * 从 views/Chat/index.vue 抽出。本模块只管「一条消息对象怎么造、怎么被事件改写」，
 * 不发起请求流（那是 useChatStream 的事），也不碰滚动（useChatScroll）。
 *
 * @param {object} deps
 * @param {import('vue').Ref<Array>} deps.messages 消息列表（页面持有，本模块只读/回写）
 * @param {import('vue').Ref<string>} deps.currentConversationId 当前会话 ID（start 事件采纳）
 * @param {import('vue').Ref<boolean>} deps.speakEnabled 语音播报开关（done 事件朗读判断）
 * @param {(ticketId: string) => void} deps.startHumanThread 转人工成功后挂接工单线程
 */
export function useChatMessages({
  messages,
  currentConversationId,
  speakEnabled,
  startHumanThread,
}) {
  let seq = 0

  function nextId() {
    return `msg-${++seq}-${Date.now()}`
  }

  function newMessage(role, content) {
    return {
      id: nextId(),
      role,
      content,
      status: role === 'user' ? 'done' : 'thinking',
      stage: 'start',
      intent: '',
      rewrites: [],
      toolCalls: [],
      sources: [],
      ticketId: '',
      errorMsg: '',
      // Bad Case 闭环：会话/trace 关联 + 反馈状态 + 追溯快照
      conversationId: '',
      traceId: '',
      cragAction: '',
      cragScore: '',
      degraded: [],
      timings: {},
      // 管理员/客服诊断展示
      sentAt: 0,
      elapsedMs: 0,
      routeSource: '',
      fromCache: '',
      // 产品消歧决策（phase15）：disambiguation 事件与 done.disambiguation 写入，
      // staff 诊断面板展示决策；clarify 时候选卡片渲染数据源
      disambiguation: null,
      // 事件时间线：start 事件已到（流式实时展示诊断），SSE 事件快照与阶段时刻
      started: false,
      eventLog: [],
      stageTs: {},
      deltaCount: 0,
      deltaChars: 0,
      deltaFirstMs: 0,
      deltaLastMs: 0,
      // 诊断信息默认展开（admin/agent 点收起）
      diagOpen: true,
      srcOpen: -1,
      feedbackScore: 0,
      feedbackComment: '',
      feedbackSubmitted: false,
      feedbackSubmitting: false,
      // 系统提示消息（如"已切换到历史会话"）：不显示反馈条
      isNotice: false,
    }
  }

  // 记录 SSE 事件到诊断时间线（delta 聚合为计数，不进列表防刷屏）
  function recordEvent(msg, name, data) {
    if (!msg.sentAt) msg.sentAt = Date.now()
    const t = Date.now() - msg.sentAt
    msg.elapsedMs = t
    if (name === 'delta') {
      msg.deltaCount += 1
      msg.deltaChars += data && data.delta ? data.delta.length : 0
      if (!msg.deltaFirstMs) msg.deltaFirstMs = t
      msg.deltaLastMs = t
      return
    }
    const last = msg.eventLog[msg.eventLog.length - 1]
    msg.eventLog.push({ name, t, dt: last ? t - last.t : 0, data: eventSnap(name, data) })
    if (!(name in msg.stageTs)) msg.stageTs[name] = t
  }

  // 处理一条 SSE 事件，更新对应客服消息
  function handleEvent(msg, name, data) {
    // 诊断时间线：每条事件落账（含相对时刻/间隔），delta 走聚合计数
    recordEvent(msg, name, data)
    switch (name) {
      case 'start':
        msg.started = true
        msg.intent = data.intent || ''
        msg.routeSource = data.route_source || ''
        msg.stage = 'start'
        // 会话 ID：每条问答唯一，反馈关联 Bad Case 用
        msg.conversationId = data.conversation_id || msg.conversationId || ''
        // 会话归属：currentConversationId 为空（新会话首轮）时采纳服务端分配的
        // conversation_id，后续提问透传同一 id，保证多轮归属同一会话
        if (!currentConversationId.value && data.conversation_id) {
          currentConversationId.value = data.conversation_id
        }
        break
      case 'retrieving':
        msg.stage = 'retrieving'
        break
      case 'rewriting':
        msg.rewrites = data.variants || []
        msg.stage = 'generating'
        break
      case 'generating':
        msg.stage = 'generating'
        break
      case 'disambiguation':
        // 产品消歧决策（phase15）：阶段切换为「正在识别产品…」，
        // 同时作为 staff 诊断面板与 clarify 候选卡片的数据源
        msg.stage = 'disambiguation'
        msg.disambiguation = { ...(data || {}) }
        break
      case 'delta':
        // 流式生成：逐 chunk 累积文本（打字机效果）；done 事件会带完整 answer 兜底
        msg.stage = 'generating'
        msg.content = (msg.content || '') + (data.delta || '')
        break
      case 'tool_call':
        // 阶段进入业务处理中（普通用户显示友好文案，不再停留在"正在思考"）
        msg.stage = 'tool'
        msg.toolCalls.push({
          tool_name: data.tool_name || '',
          arguments: data.arguments || {},
          ticketId: '',
        })
        break
      case 'tool_result': {
        const tc = msg.toolCalls[msg.toolCalls.length - 1]
        if (tc) {
          // 保存工具结果（订单/物流/商品/退款卡片渲染用）
          tc.result = data.result
          const ticketId = extractTicketId(data.result)
          if (ticketId) {
            tc.ticketId = ticketId
            msg.ticketId = ticketId
            // transfer_human 成功（工具名匹配）：聊天流挂接人工客服线程（工单回复轮询）
            const isTransfer =
              data.tool_name === 'transfer_human' ||
              msg.toolCalls.some((t) => t.tool_name === 'transfer_human')
            if (isTransfer) startHumanThread(ticketId)
          }
        }
        break
      }
      case 'done':
        msg.stage = 'done'
        msg.status = 'done'
        msg.content = data.answer || ''
        // 总耗时 = 发送时刻 → done 时刻（前端计时，覆盖 chat/faq/task 三类意图）
        if (msg.sentAt) msg.elapsedMs = Date.now() - msg.sentAt
        msg.fromCache = data.from_cache || ''
        if (Array.isArray(data.sources)) msg.sources = data.sources
        msg.conversationId = data.conversation_id || msg.conversationId || ''
        msg.traceId = data.trace_id || ''
        // 追溯快照：CRAG 决策与降级项（提交反馈随 payload 入库）
        msg.cragAction = data.crag_action || ''
        // 诊断：CRAG 评估分数 + 后端阶段耗时（faq 检索/生成/总耗时）
        msg.cragScore = data.crag_score != null && data.crag_score !== '' ? data.crag_score : ''
        msg.timings = data.timings && typeof data.timings === 'object' ? data.timings : {}
        msg.degraded = Array.isArray(data.degraded) ? data.degraded : []
        // 消歧反问回合：done 携带 disambiguation（clarify 候选卡片数据源）
        if (data.disambiguation) msg.disambiguation = data.disambiguation
        if (data.ticket_id) msg.ticketId = data.ticket_id
        // 语音播报：开启开关时朗读最终答案（纯文本，markdown 源串朗读）
        if (speakEnabled.value && msg.content) speak(msg.content)
        break
      default:
        break
    }
  }

  function handleError(msg, message) {
    if (msg.status === 'done') return
    msg.status = 'error'
    msg.stage = 'error'
    msg.errorMsg = message || '服务异常，请稍后重试'
  }

  // 提交本条问答的满意度反馈（Bad Case 收集入口）
  async function submitMessageFeedback(m) {
    if (m.feedbackSubmitting) return
    // 未打分：显式提示而不是静默置灰（用户不知道按钮为什么点不动）
    if (!m.feedbackScore) {
      ElMessage.warning('请先选择"有帮助"或"没帮助"再提交')
      return
    }
    // 用户问题：本条客服消息的前一条用户消息
    const idx = messages.value.indexOf(m)
    const userMsg = idx > 0 ? messages.value[idx - 1] : null
    m.feedbackSubmitting = true
    try {
      await submitFeedback({
        score: m.feedbackScore,
        comment: m.feedbackComment || '',
        conversation_id: m.conversationId || '',
        trace_id: m.traceId || '',
        query: userMsg ? userMsg.content : '',
        answer: m.content || '',
        sources: m.sources || [],
        intent: m.intent || '',
        crag_action: m.cragAction || '',
        degraded: m.degraded || [],
      })
      m.feedbackSubmitted = true
      ElMessage.success('感谢您的反馈！')
    } catch (e) {
      // 用户提示已由 request 拦截器统一处理（ElMessage）；此处补控制台观测
      console.warn('[Chat] 反馈提交失败', e)
    } finally {
      m.feedbackSubmitting = false
    }
  }

  return { newMessage, recordEvent, handleEvent, handleError, submitMessageFeedback }
}
