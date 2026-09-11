import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

// ---------- mock 依赖 ----------

const chatApi = vi.hoisted(() => ({
  chatStream: vi.fn(),
  fetchPersonas: vi.fn(),
}))

vi.mock('@/api/chat', () => chatApi)

// 历史会话 API（抽屉数据源）
const conversationApi = vi.hoisted(() => ({
  listConversations: vi.fn(),
  listMessages: vi.fn(),
}))

vi.mock('@/api/conversation', () => conversationApi)

// auth store：组件用 role 区分 staff（知识来源可见性）与 user（快捷订单号跟随）
const authMock = vi.hoisted(() => ({
  role: 'admin',
  user: { username: 'admin' },
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => authMock,
}))

// 人格 store：mock 工厂内用 Vue reactive 构造（与真实 Pinia setup store 同响应式语义），
// 保证 setPersonas 后模板 v-for 重新渲染
vi.mock('@/stores/persona', async () => {
  const { reactive } = await import('vue')
  const store = reactive({
    personaId: '',
    personas: [],
    loaded: false,
    setPersona(id) {
      store.personaId = id || ''
    },
    setPersonas(list) {
      store.personas = Array.isArray(list) ? list : []
      store.loaded = true
    },
  })
  return { usePersonaStore: () => store }
})

const feedbackApi = vi.hoisted(() => ({
  submitFeedback: vi.fn(),
}))

vi.mock('@/api/feedback', () => feedbackApi)

const ElMessageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('element-plus', () => ({ ElMessage: ElMessageMock }))

import Chat from '@/views/Chat/index.vue'
import { usePersonaStore } from '@/stores/persona'

// ---------- 组件测试的 EP 组件 stub ----------

const stubs = {
  'el-card': {
    template:
      '<div class="el-card-stub"><div class="el-card-header"><slot name="header" /></div><div class="el-card-body"><slot /></div></div>',
  },
  'el-input': {
    props: ['modelValue'],
    emits: ['update:modelValue', 'keydown'],
    template:
      '<input class="el-input-stub" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @keydown="$emit(\'keydown\', $event)" />',
  },
  'el-button': {
    props: ['disabled', 'loading'],
    emits: ['click'],
    template:
      '<button class="el-button-stub" :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
  },
  'el-tag': { template: '<span class="el-tag-stub"><slot /></span>' },
  'el-select': {
    props: ['modelValue', 'placeholder'],
    template: '<select class="el-select-stub" :placeholder="placeholder"><slot /></select>',
  },
  'el-option': {
    props: ['label', 'value'],
    template: '<option class="el-option-stub" :value="value">{{ label }}</option>',
  },
  'el-empty': {
    props: ['description'],
    template:
      '<div class="el-empty-stub"><span class="el-empty-description">{{ description }}</span><slot /></div>',
  },
  'el-drawer': {
    props: ['modelValue', 'title', 'size'],
    emits: ['update:modelValue'],
    template:
      '<div v-if="modelValue" class="el-drawer-stub"><div class="el-drawer-title">{{ title }}</div><div class="el-drawer-body"><slot /></div></div>',
  },
  'el-alert': {
    props: ['title'],
    template:
      '<div class="el-alert-stub"><slot name="title" /><span class="el-alert-title">{{ title }}</span><slot /></div>',
  },
  'el-icon': { template: '<span class="el-icon-stub"><slot /></span>' },
  Loading: { template: '<i class="icon-stub" />' },
  'router-link': { template: '<a class="router-link-stub"><slot /></a>' },
  'el-descriptions': { template: '<div class="el-descriptions-stub"><slot /></div>' },
  'el-descriptions-item': {
    props: ['label'],
    template:
      '<div class="el-descriptions-item-stub"><span class="el-descriptions-label">{{ label }}</span><slot /></div>',
  },
  'el-timeline': { template: '<div class="el-timeline-stub"><slot /></div>' },
  'el-timeline-item': {
    props: ['timestamp'],
    template:
      '<div class="el-timeline-item-stub"><span class="el-timeline-timestamp">{{ timestamp }}</span><slot /></div>',
  },
}

describe('Chat 组件', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    chatApi.chatStream.mockResolvedValue()
    chatApi.fetchPersonas.mockResolvedValue({
      personas: [
        { id: 'professional', name: '专业客服', description: '严谨简洁' },
        { id: 'gentle', name: '温柔客服', description: '亲切耐心' },
        { id: 'lively', name: '活泼客服', description: '热情俏皮' },
      ],
    })
    // 历史会话样例（updated_at 倒序）
    conversationApi.listConversations.mockResolvedValue({
      conversations: [
        {
          id: 'conv-1',
          title: '退货政策咨询',
          created_at: '2026-08-28T10:00:00',
          updated_at: '2026-08-28T10:05:00',
          message_count: 2,
        },
        {
          id: 'conv-2',
          title: '物流查询',
          created_at: '2026-08-27T09:00:00',
          updated_at: '2026-08-27T09:10:00',
          message_count: 4,
        },
      ],
      total: 2,
    })
    conversationApi.listMessages.mockResolvedValue({
      conversation_id: 'conv-1',
      messages: [
        { role: 'user', content: '退货多久到账？', intent: 'faq', created_at: '2026-08-28T10:00:00' },
        { role: 'assistant', content: '48 小时内原路退回。', intent: 'faq', created_at: '2026-08-28T10:00:05' },
      ],
    })
    const personaStore = usePersonaStore()
    personaStore.personaId = ''
    personaStore.personas = []
    personaStore.loaded = false
  })

  function mountChat() {
    return mount(Chat, { global: { components: stubs } })
  }

  function sendButton(wrapper) {
    return wrapper.find('.ai-send')
  }

  async function typeAndSend(wrapper, text) {
    await wrapper.find('.el-input-stub').setValue(text)
    await sendButton(wrapper).trigger('click')
    await flushPromises()
  }

  // 打开历史会话抽屉（点卡片 header 的"历史会话"按钮并等待列表加载）
  async function openHistory(wrapper) {
    const histBtn = wrapper.findAll('.el-button-stub').find((b) => b.text() === '历史会话')
    await histBtn.trigger('click')
    await flushPromises()
  }

  // 抽屉内的按钮（与新对话/查看/继续对话交互）
  function drawerButtons(wrapper) {
    return wrapper.findAll('.el-drawer-stub .el-button-stub')
  }

  it('组件名为 Chat：keep-alive include 契约（路由动态导入下缓存匹配依赖此名）', () => {
    expect(Chat.name).toBe('Chat')
  })

  it('渲染消息列表空态、快捷提问引导条、转人工入口与输入框', async () => {
    const wrapper = mountChat()
    await nextTick()

    const text = wrapper.text()
    expect(text).toContain('华为 Mate 70 Pro 多少钱')
    expect(text).toContain('查一下订单 20260801001')
    expect(text).toContain('物流到哪了')
    // 退货与转人工不占快捷建议位，转人工为常驻入口按钮
    expect(wrapper.vm.quickQuestions.find((q) => q.text === '我要退货')).toBeUndefined()
    expect(wrapper.findAll('.el-button-stub').some((b) => b.text() === '转人工')).toBe(true)
    expect(wrapper.find('.el-input-stub').exists()).toBe(true)
    expect(sendButton(wrapper)).toBeTruthy()
    // 空输入时发送按钮禁用
    expect(sendButton(wrapper).attributes('disabled')).toBeDefined()
  })

  it('点击快捷提问填充输入框', async () => {
    const wrapper = mountChat()
    const btn = wrapper
      .findAll('.el-button-stub')
      .find((b) => b.text().includes('物流到哪了'))
    await btn.trigger('click')
    expect(wrapper.vm.inputText).toBe('物流到哪了')
  })

  it('点击顶部常驻"转人工"：直接发起转人工会话', async () => {
    const wrapper = mountChat()
    await nextTick()
    const humanBtn = wrapper.findAll('.el-button-stub').find((b) => b.text() === '转人工')
    expect(humanBtn).toBeTruthy()
    await humanBtn.trigger('click')
    await flushPromises()

    expect(chatApi.chatStream).toHaveBeenCalledTimes(1)
    const [q] = chatApi.chatStream.mock.calls[0]
    expect(q).toBe('帮我转人工客服')
    // 复用走主链路：消息列表出现用户提问
    expect(wrapper.find('.bubble.user').text()).toContain('帮我转人工客服')
  })

  it('发送消息调用 chatStream，携带 query 与空 history 并清空输入框', async () => {
    const wrapper = mountChat()
    await typeAndSend(wrapper, '华为 Mate 70 Pro 多少钱')

    expect(chatApi.chatStream).toHaveBeenCalledTimes(1)
    const [q, opts] = chatApi.chatStream.mock.calls[0]
    expect(q).toBe('华为 Mate 70 Pro 多少钱')
    expect(opts.history).toEqual([])
    expect(wrapper.vm.inputText).toBe('')
  })

  it('Enter 键发送消息', async () => {
    const wrapper = mountChat()
    const input = wrapper.find('.el-input-stub')
    await input.setValue('查一下订单 20260801001')
    await input.trigger('keydown', { key: 'Enter' })

    expect(chatApi.chatStream).toHaveBeenCalledWith(
      '查一下订单 20260801001',
      expect.objectContaining({ history: [] }),
    )
  })

  it('SSE 事件驱动渲染：faq 意图展示回答与知识来源列表', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'faq' })
      onEvent('retrieving', {})
      onEvent('generating', {})
      onEvent('done', {
        answer: '华为 Mate 70 Pro 起售价 **6999 元**',
        sources: [
          { title: '华为 Mate 70 系列价格说明', snippet: '…' },
          { title: 'Mate 70 Pro 商品规格', snippet: '…' },
        ],
      })
      onDone({ answer: '华为 Mate 70 Pro 起售价 **6999 元**' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '华为 Mate 70 Pro 多少钱')

    expect(wrapper.text()).toContain('华为 Mate 70 Pro 起售价')
    expect(wrapper.text()).toContain('知识来源')
    expect(wrapper.text()).toContain('华为 Mate 70 系列价格说明')
    expect(wrapper.text()).toContain('Mate 70 Pro 商品规格')
    // markdown 加粗渲染
    expect(wrapper.find('.md-body strong').exists()).toBe(true)
    // 用户消息也在列表中
    expect(wrapper.find('.bubble.user').text()).toContain('华为 Mate 70 Pro 多少钱')
  })

  it('SSE 事件驱动渲染：task 意图展示工具调用过程与售后工单提示', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'task' })
      onEvent('tool_call', {
        tool_name: 'create_ticket',
        arguments: { title: '退货申请', description: '我要退货', priority: 'medium' },
      })
      onEvent('tool_result', {
        tool_name: 'create_ticket',
        result: { ticket_id: 'TK-20260805001', created: true },
      })
      onEvent('done', { answer: '已为您提交退货工单，售后人员将尽快处理。' })
      onDone({ answer: '已为您提交退货工单，售后人员将尽快处理。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '我要退货')

    expect(wrapper.text()).toContain('调用 创建工单')
    expect(wrapper.text()).toContain('TK-20260805001')
    expect(wrapper.text()).toContain('已创建工单')
    expect(wrapper.text()).toContain('工单列表')
    expect(wrapper.find('.router-link-stub').exists()).toBe(true)
  })

  it('SSE 工具结果卡片：query_order 渲染订单卡片', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'task' })
      onEvent('tool_call', { tool_name: 'query_order', arguments: { order_sn: '20260801001' } })
      onEvent('tool_result', {
        tool_name: 'query_order',
        result: {
          order_sn: '20260801001',
          status: '已发货',
          items: [{ product_id: 'P001', name: '华为 Mate 70 Pro', spec: '256G 曜石黑', price: 6999, quantity: 1 }],
          pay_amount: 6999,
          logistics_no: 'SF1234567890',
          created_at: '2026-08-01 09:30:00',
        },
      })
      onEvent('done', { answer: '您的订单 20260801001 已发货。' })
      onDone({ answer: '您的订单 20260801001 已发货。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '查一下订单 20260801001')

    const text = wrapper.text()
    // 订单卡片渲染（el-descriptions stub）
    expect(wrapper.find('.tool-result-card').exists()).toBe(true)
    expect(text).toContain('订单号')
    expect(text).toContain('20260801001')
    expect(text).toContain('已发货')
    expect(text).toContain('6999')
    expect(text).toContain('SF1234567890')
    expect(text).toContain('华为 Mate 70 Pro')
  })

  it('SSE 工具结果卡片：query_logistics 渲染物流轨迹卡片', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'task' })
      onEvent('tool_call', { tool_name: 'query_logistics', arguments: { order_sn: '20260801001' } })
      onEvent('tool_result', {
        tool_name: 'query_logistics',
        result: [
          { ts: '2026-08-01 16:00:00', content: '已揽收' },
          { ts: '2026-08-01 18:30:00', content: '运输中（预计明天送达）' },
        ],
      })
      onEvent('done', { answer: '您的订单 20260801001 物流轨迹：已揽收 → 运输中。' })
      onDone({ answer: '您的订单 20260801001 物流轨迹：已揽收 → 运输中。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '物流到哪了')

    const text = wrapper.text()
    // 物流轨迹卡片（el-timeline stub 渲染 ts + content）
    expect(wrapper.find('.el-timeline-stub').exists()).toBe(true)
    expect(text).toContain('2026-08-01 16:00:00')
    expect(text).toContain('已揽收')
    expect(text).toContain('2026-08-01 18:30:00')
    expect(text).toContain('运输中（预计明天送达）')
  })

  it('普通用户（user）不暴露工具调用/改写等内部机制：过程行隐藏、结果卡片保留、友好提示', async () => {
    authMock.role = 'user' // isStaff = false：内部机制（工具名/参数/改写标签）对其隐藏
    let emit = null
    try {
      chatApi.chatStream.mockImplementation((q, opts) => {
        emit = opts.onEvent
        opts.onEvent('start', { query: q, intent: 'task' })
        opts.onEvent('tool_call', { tool_name: 'query_order', arguments: { order_sn: '20260801001' } })
        return Promise.resolve()
      })

      const wrapper = mountChat()
      await typeAndSend(wrapper, '查一下订单 20260801001')

      // 流式处理中：友好提示，且不出现"工具调用/调用 查询订单"等内部字样
      expect(wrapper.text()).toContain('正在为您处理')
      expect(wrapper.text()).not.toContain('工具调用')
      expect(wrapper.text()).not.toContain('调用 查询订单')

      // 补发结果与完成：业务结果卡片对普通用户保留
      emit('tool_result', {
        tool_name: 'query_order',
        result: {
          order_sn: '20260801001',
          status: '已发货',
          pay_amount: 6999,
          logistics_no: 'SF1234567890',
          items: [{ name: '华为 Mate 70 Pro', spec: '256G', price: 6999, quantity: 1 }],
        },
      })
      emit('done', { answer: '您的订单 20260801001 已发货。' })
      await flushPromises()

      expect(wrapper.find('.tool-result-card').exists()).toBe(true)
      const doneText = wrapper.text()
      expect(doneText).toContain('SF1234567890')
      expect(doneText).not.toContain('工具调用')
    } finally {
      authMock.role = 'admin'
    }
  })

  it('无工具结果时保持 JSON 文本回退（不渲染卡片）', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'task' })
      onEvent('tool_call', { tool_name: 'search_knowledge', arguments: { query: q } })
      onEvent('tool_result', {
        tool_name: 'search_knowledge',
        result: [{ doc_id: 'doc1', title: '退货政策', text: '…', score: 0.9 }],
      })
      onEvent('done', { answer: '根据售后政策，订单已发货/已完成可申请退货。' })
      onDone({ answer: '根据售后政策，订单已发货/已完成可申请退货。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '怎么退货')

    // search_knowledge 结果不匹配任何卡片类型 → 不渲染卡片，保持文本
    expect(wrapper.find('.tool-result-card').exists()).toBe(false)
  })

  it('SSE error 事件渲染错误提示', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onError }) => {
      onEvent('error', { message: '服务暂时不可用' })
      onError('服务暂时不可用')
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '你好')

    expect(wrapper.text()).toContain('服务暂时不可用')
  })

  it('多轮对话把历史上下文传给后端', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'chat' })
      onEvent('generating', {})
      onEvent('done', { answer: '好的，已收到。' })
      onDone({ answer: '好的，已收到。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '第一问')
    await typeAndSend(wrapper, '第二问')

    expect(chatApi.chatStream).toHaveBeenCalledTimes(2)
    const [q2, opts2] = chatApi.chatStream.mock.calls[1]
    expect(q2).toBe('第二问')
    expect(opts2.history).toEqual([
      { role: 'user', content: '第一问' },
      { role: 'assistant', content: '好的，已收到。' },
    ])
  })

  it('流式请求进行中按钮变为「停止生成」，点击中止在途流并保留已生成内容', async () => {
    let capturedOpts = null
    let resolveStream = null
    chatApi.chatStream.mockImplementation((q, opts) => {
      capturedOpts = opts
      opts.onEvent('start', { query: q, intent: 'faq' })
      opts.onEvent('generating', {})
      opts.onEvent('delta', { delta: '已生成的部分回答' })
      // 模拟真实 fetch：signal.abort() 时触发 onError（AbortError → 发送已取消）
      return new Promise((resolve) => {
        const onAbort = () => {
          opts.signal.removeEventListener('abort', onAbort)
          opts.onError('发送已取消')
          resolve()
        }
        opts.signal.addEventListener('abort', onAbort)
        resolveStream = resolve
      })
    })

    const wrapper = mountChat()
    await wrapper.find('.el-input-stub').setValue('测试问题')
    await sendButton(wrapper).trigger('click')
    await flushPromises()

    // 流式中：按钮为停止态（可点、红色方块）
    const stopBtn = sendButton(wrapper)
    expect(stopBtn.attributes('disabled')).toBeUndefined()
    expect(stopBtn.classes()).toContain('is-stop')

    // 点击停止 → 在途流被中止
    await stopBtn.trigger('click')
    await flushPromises()
    expect(capturedOpts.signal).toBeTruthy()
    expect(capturedOpts.signal.aborted).toBe(true)

    // 已生成内容保留且气泡正常收尾：无错误红条、不出现「发送已取消」文案
    expect(wrapper.text()).toContain('已生成的部分回答')
    expect(wrapper.text()).not.toContain('发送已取消')
    expect(wrapper.find('.error-alert').exists()).toBe(false)
    expect(wrapper.vm.streaming).toBe(false)

    // 停止后可立即再发送（按钮恢复发送态）
    resolveStream && resolveStream()
    await flushPromises()
    expect(sendButton(wrapper).classes()).not.toContain('is-stop')
  })

  it('完成后可提交反馈：打分 + 评论随 conversation_id/trace_id 上传', async () => {
    feedbackApi.submitFeedback.mockResolvedValue({ feedback_id: 'fb-1', created: true })
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'faq', conversation_id: 'conv-1' })
      onEvent('retrieving', {})
      onEvent('generating', {})
      onEvent('done', {
        answer: '退货到账约 48 小时。',
        sources: [{ doc_id: 'mall/business.md', title: '退货规则' }],
        trace_id: 'trace-1',
        conversation_id: 'conv-1',
      })
      onDone({ answer: '退货到账约 48 小时。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '退货多久到账？')

    // 完成后渲染评分条（口袋赞踩 / 评论 / 提交）
    const helpful = wrapper.find('.fb-btn')
    expect(helpful).toBeTruthy()

    await helpful.trigger('click') // 有帮助 → score 5
    await wrapper.find('.fb-comment').setValue('很好')
    const submitBtn = wrapper.find('.fb-submit')
    await submitBtn.trigger('click')
    await flushPromises()

    expect(feedbackApi.submitFeedback).toHaveBeenCalledWith({
      score: 5,
      comment: '很好',
      conversation_id: 'conv-1',
      trace_id: 'trace-1',
      query: '退货多久到账？',
      answer: '退货到账约 48 小时。',
      sources: [{ doc_id: 'mall/business.md', title: '退货规则' }],
      intent: 'faq',
      crag_action: '',
      degraded: [],
    })
    await nextTick()
    expect(wrapper.text()).toContain('已提交反馈')
  })

  it('未打分时点击提交给出明确提示且不上报', async () => {
    feedbackApi.submitFeedback.mockResolvedValue({ feedback_id: 'fb-1', created: true })
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'faq', conversation_id: 'conv-2' })
      onEvent('done', { answer: '这没有问题', conversation_id: 'conv-2' })
      onDone({ answer: '这没有问题' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '你好')

    const submitBtn = wrapper.find('.fb-submit')
    // 未打分时按钮可点（显式提示而不是静默置灰），点击后提示且不上报
    expect(submitBtn.attributes('disabled')).toBeUndefined()
    await submitBtn.trigger('click')
    expect(ElMessageMock.warning).toHaveBeenCalledWith(expect.stringContaining('请先选择'))
    expect(feedbackApi.submitFeedback).not.toHaveBeenCalled()
  })

  it('来源列表展示命中分数，并可展开溯因片段全文（诊断面板内）', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'faq', conversation_id: 'conv-3', route_source: 'rule' })
      onEvent('retrieving', {})
      onEvent('generating', {})
      onEvent('done', {
        answer: '48 小时内。',
        sources: [
          {
            title: '退货规则',
            score: 0.92,
            text: '退货款项在申请通过后 48 小时内原路退回。',
            snippet: '退货款项在申请通过后…',
          },
        ],
        crag_action: 'generate',
        crag_score: 0.92,
        degraded: [],
        conversation_id: 'conv-3',
      })
      onDone({ answer: '48 小时内。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '退货多久到账？')
    await nextTick()

    const text = wrapper.text()
    // 顺序：先回答，后诊断信息（知识来源在面板内）
    expect(text.indexOf('48 小时内。')).toBeGreaterThan(-1)
    expect(text.indexOf('知识来源')).toBeGreaterThan(text.indexOf('48 小时内。'))

    // 管理员（admin）诊断面板默认展开，来源列表直接可见
    expect(wrapper.find('.diag-header').exists()).toBe(true)
    expect(text).toContain('知识来源（1 条，最终排序）')
    const sources = wrapper.findAll('.diag-source')
    expect(sources.length).toBe(1)

    // 分数展示
    expect(text).toContain('0.92')

    // 展开溯因：片段全文出现
    const toggle = wrapper.findAll('.el-button-stub').find((b) => b.text() === '溯因')
    expect(toggle).toBeTruthy()
    await toggle.trigger('click')
    await nextTick()
    expect(wrapper.text()).toContain('退货款项在申请通过后 48 小时内原路退回。')

    // 收起：片段全文隐藏
    const collapse = wrapper.findAll('.el-button-stub').find((b) => b.text() === '收起')
    await collapse.trigger('click')
    await nextTick()
    expect(wrapper.text()).not.toContain('退货款项在申请通过后 48 小时内原路退回。')
  })

  it('管理员诊断面板：流式中实时显示阶段，完成后展示事件时间线与后端阶段耗时', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'faq', route_source: 'rule', role: 'admin' })
      // 流式未收口：不触发 done，模拟正在处理（定位"卡住"环节的场景）
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '华为 Mate 70 Pro 多少钱')

    // 流式中：诊断面板已出现（started），带实时"正在思考…"徽标
    const liveChip = wrapper.find('.diag-chip.chip-live')
    expect(wrapper.find('.diag-section').exists()).toBe(true)
    expect(liveChip.exists()).toBe(true)
    expect(liveChip.text()).toContain('正在思考')
  })

  it('管理员诊断面板：done 后事件时间线/阶段耗时/CRAG 分数齐备，可复制完整诊断 JSON', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'faq', route_source: 'rule', role: 'admin' })
      onEvent('retrieving', {})
      onEvent('rewriting', { variants: ['华为 Mate 70 Pro 多少钱', '华为 Mate 70 Pro 价格'] })
      onEvent('generating', {})
      onEvent('delta', { delta: '华为' })
      onEvent('done', {
        answer: '华为 Mate 70 Pro 起售价 6999 元。',
        crag_action: 'generate',
        crag_score: 0.92,
        degraded: ['reranker'],
        timings: { retrieve_ms: 42, generate_ms: 120, total_ms: 170 },
        sources: [{ title: '价格说明', score: 0.93 }],
        trace_id: 'trace-faq-9',
        conversation_id: 'conv-diag',
      })
      onDone({ answer: '华为 Mate 70 Pro 起售价 6999 元。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '华为 Mate 70 Pro 多少钱')
    await nextTick()

    const text = wrapper.text()
    // 徽标：意图/路由/CRAG 分数/降级
    expect(text).toContain('知识问答')
    expect(text).toContain('规则命中')
    expect(text).toContain('CRAG 0.92')
    expect(text).toContain('降级：reranker')
    // 阶段耗时（后端计时）：检索/生成/总耗时
    expect(text).toContain('阶段耗时（后端计时）')
    expect(text).toContain('42ms')
    expect(text).toContain('120ms')
    // 事件时间线：开始/检索/改写/生成中/流式输出/完成 + 意图转发照
    expect(text).toContain('事件时间线')
    expect(text).toContain('开始')
    expect(text).toContain('检索')
    expect(text).toContain('改写')
    expect(text).toContain('流式输出')
    expect(text).toContain('完成')
    expect(text).toContain('意图: 知识问答 · 规则命中 · role=admin')
    // 改写变体明细
    expect(text).toContain('查询改写变体（二次检索用）')
    expect(text).toContain('华为 Mate 70 Pro 价格')
    // 会话上下文
    expect(text).toContain('当前用户')
    expect(text).toContain('admin')

    // 复制完整诊断 JSON：触发剪贴板写入且含诊断字段
    const clipboard = { writeText: vi.fn().mockResolvedValue() }
    Object.defineProperty(navigator, 'clipboard', { value: clipboard, configurable: true })
    const copyBtn = wrapper.findAll('.el-button-stub').find((b) => b.text().includes('完整诊断'))
    expect(copyBtn).toBeTruthy()
    await copyBtn.trigger('click')
    await nextTick()
    expect(clipboard.writeText).toHaveBeenCalledTimes(1)
    const blob = clipboard.writeText.mock.calls[0][0]
    expect(blob).toContain('"crag_score": 0.92')
    expect(blob).toContain('"backend_timings_ms"')
    expect(blob).toContain('"intent": "faq"')
    expect(blob).toContain('"t_ms"')
  })

  // ---------- 历史会话抽屉 ----------

  it('点"历史会话"：打开抽屉并渲染会话列表（标题/更新时间/条数）', async () => {
    const wrapper = mountChat()
    await flushPromises()

    const histBtn = wrapper.findAll('.el-button-stub').find((b) => b.text() === '历史会话')
    expect(histBtn).toBeTruthy()
    await openHistory(wrapper)

    // 打开即拉列表（limit/offset 默认 20/0 由 api/conversation.js 提供）
    expect(conversationApi.listConversations).toHaveBeenCalledTimes(1)
    const drawer = wrapper.find('.el-drawer-stub')
    expect(drawer.exists()).toBe(true)
    expect(drawer.text()).toContain('历史会话')
    const text = drawer.text()
    expect(text).toContain('退货政策咨询')
    expect(text).toContain('物流查询')
    // 更新时间（ISO → YYYY-MM-DD HH:mm）
    expect(text).toContain('2026-08-28 10:05')
    expect(text).toContain('2026-08-27 09:10')
    // 消息条数
    expect(text).toContain('2 条消息')
    expect(text).toContain('4 条消息')
    // 每项有查看/继续对话两个动作
    expect(drawerButtons(wrapper).filter((b) => b.text() === '查看').length).toBe(2)
    expect(drawerButtons(wrapper).filter((b) => b.text() === '继续对话').length).toBe(2)
  })

  it('无历史会话时抽屉显示空态文案', async () => {
    conversationApi.listConversations.mockResolvedValue({ conversations: [], total: 0 })
    const wrapper = mountChat()
    await flushPromises()
    await openHistory(wrapper)

    expect(wrapper.find('.el-drawer-stub').text()).toContain('暂无历史会话')
  })

  it('「查看」历史会话：抽屉内按正序渲染该会话消息（含角色名）', async () => {
    const wrapper = mountChat()
    await flushPromises()
    await openHistory(wrapper)

    const viewBtn = drawerButtons(wrapper).find((b) => b.text() === '查看')
    expect(viewBtn).toBeTruthy()
    await viewBtn.trigger('click')
    await flushPromises()

    expect(conversationApi.listMessages).toHaveBeenCalledWith('conv-1')
    const msgs = wrapper.findAll('.viewed-msg')
    expect(msgs.length).toBe(2)
    const roles = wrapper.findAll('.viewed-role').map((r) => r.text())
    expect(roles).toEqual(['用户', '客服'])
    // 正序：用户提问在客服回答之前
    const text = wrapper.text()
    expect(text).toContain('退货多久到账？')
    expect(text).toContain('48 小时内原路退回。')
    expect(text.indexOf('退货多久到账？')).toBeLessThan(text.indexOf('48 小时内原路退回。'))
  })

  it('「继续对话」：关闭抽屉、重建 history，再发问时 chatStream 透传 conversation_id', async () => {
    const wrapper = mountChat()
    await flushPromises()
    await openHistory(wrapper)

    const contBtn = drawerButtons(wrapper).find((b) => b.text() === '继续对话')
    await contBtn.trigger('click')
    await flushPromises()

    // 抽屉关闭；主消息区清空并插入切换提示
    expect(wrapper.find('.el-drawer-stub').exists()).toBe(false)
    expect(wrapper.text()).toContain('已切换到历史会话，可继续提问')
    // history 重建为该会话既有消息（role/content 映射、正序）
    expect(wrapper.vm.history).toEqual([
      { role: 'user', content: '退货多久到账？' },
      { role: 'assistant', content: '48 小时内原路退回。' },
    ])

    // 再发问：请求透传该会话 id，history 前置历史消息
    await typeAndSend(wrapper, '那取消订单呢？')
    const [q, opts] = chatApi.chatStream.mock.calls[0]
    expect(q).toBe('那取消订单呢？')
    expect(opts.conversationId).toBe('conv-1')
    expect(opts.history).toEqual([
      { role: 'user', content: '退货多久到账？' },
      { role: 'assistant', content: '48 小时内原路退回。' },
    ])
  })

  it('start 事件携带 conversation_id：首轮采纳，同会话多轮透传同一 id', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'faq', conversation_id: 'conv-live' })
      onEvent('done', { answer: '答案内容', conversation_id: 'conv-live' })
      onDone({ answer: '答案内容' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '第一问')
    await typeAndSend(wrapper, '第二问')

    const [, opts1] = chatApi.chatStream.mock.calls[0]
    const [, opts2] = chatApi.chatStream.mock.calls[1]
    expect(opts1.conversationId).toBe('')
    expect(opts2.conversationId).toBe('conv-live')
  })

  it('"新对话"：清空 history/currentConversationId/消息列表并关闭抽屉', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'faq', conversation_id: 'conv-live' })
      onEvent('done', { answer: '答案内容', conversation_id: 'conv-live' })
      onDone({ answer: '答案内容' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '第一问')
    expect(wrapper.vm.currentConversationId).toBe('conv-live')
    expect(wrapper.vm.messages.length).toBe(2)

    await openHistory(wrapper)
    const newBtn = drawerButtons(wrapper).find((b) => b.text() === '新对话')
    expect(newBtn).toBeTruthy()
    await newBtn.trigger('click')
    await flushPromises()

    expect(wrapper.find('.el-drawer-stub').exists()).toBe(false)
    expect(wrapper.vm.history).toEqual([])
    expect(wrapper.vm.currentConversationId).toBe('')
    expect(wrapper.vm.messages).toEqual([])
    // 回到空态
    expect(wrapper.find('.chat-empty').exists()).toBe(true)
  })

  it('流式进行中「新对话」被阻止：不清空消息，旧流现场保留', async () => {
    // 流永不结束（无 done）：保持 streaming=true
    chatApi.chatStream.mockImplementation(() => new Promise(() => {}))
    const wrapper = mountChat()
    await typeAndSend(wrapper, '进行中的提问')
    expect(wrapper.vm.streaming).toBe(true)

    await openHistory(wrapper)
    const newBtn = drawerButtons(wrapper).find((b) => b.text() === '新对话')
    await newBtn.trigger('click')
    await flushPromises()

    // 未清空：消息与在途流原样保留（避免旧 start 会话 id 被误采纳为新会话）
    expect(wrapper.vm.messages.length).toBeGreaterThan(0)
  })

  it('流式进行中「继续对话」被阻止：不切换会话、不重建 history', async () => {
    chatApi.chatStream.mockImplementation(() => new Promise(() => {}))
    const wrapper = mountChat()
    await typeAndSend(wrapper, '进行中的提问')
    expect(wrapper.vm.streaming).toBe(true)
    conversationApi.listMessages.mockClear()

    await openHistory(wrapper)
    const contBtn = drawerButtons(wrapper).find((b) => b.text() === '继续对话')
    await contBtn.trigger('click')
    await flushPromises()

    expect(wrapper.vm.currentConversationId).toBe('')
    expect(conversationApi.listMessages).not.toHaveBeenCalled()
  })

  // ---------- 语音 AI 集成（Web Speech ASR / TTS）----------

  it('挂载时加载人格列表（语气选择器数据源）', async () => {
    const wrapper = mountChat()
    await flushPromises()
    await nextTick()

    expect(chatApi.fetchPersonas).toHaveBeenCalledTimes(1)
    const personaStore = usePersonaStore()
    expect(personaStore.personas.map((p) => p.id)).toEqual(['professional', 'gentle', 'lively'])
    // 选择器渲染人格选项
    const options = wrapper.findAll('option.el-option-stub')
    expect(options.map((o) => o.text())).toEqual(['专业客服', '温柔客服', '活泼客服'])
  })

  it('人格列表加载失败不影响聊天主链路', async () => {
    chatApi.fetchPersonas.mockRejectedValue(new Error('网络失败'))
    const wrapper = mountChat()
    await flushPromises()

    expect(wrapper.find('.el-input-stub').exists()).toBe(true)
    expect(sendButton(wrapper)).toBeTruthy()
  })

  it('选择人格后发送：persona 随请求体传给 chatStream', async () => {
    const wrapper = mountChat()
    await flushPromises()
    usePersonaStore().personaId = 'lively'
    await nextTick()

    await typeAndSend(wrapper, '你好')

    expect(chatApi.chatStream).toHaveBeenCalledWith(
      '你好',
      expect.objectContaining({ persona: 'lively' }),
    )
  })

  it('未选人格发送：persona 传 null（默认语气）', async () => {
    const wrapper = mountChat()
    await flushPromises()

    await typeAndSend(wrapper, '你好')

    expect(chatApi.chatStream).toHaveBeenCalledWith(
      '你好',
      expect.objectContaining({ persona: null }),
    )
  })

  it('不支持语音的浏览器：麦克风保留但点击提示；播报按钮隐藏', async () => {
    vi.stubGlobal('webkitSpeechRecognition', undefined)
    vi.stubGlobal('SpeechRecognition', undefined)
    vi.stubGlobal('speechSynthesis', undefined)
    const { ElMessage } = await import('element-plus')

    const wrapper = mountChat()
    await nextTick()

    // 规范：麦克风按钮保留，点击后弹提示（不静默隐藏）
    expect(wrapper.find('.ai-mic').exists()).toBe(true)
    await wrapper.find('.ai-mic').trigger('click')
    expect(ElMessage.warning).toHaveBeenCalledWith(
      expect.stringContaining('不支持语音识别')
    )
    // 无 speechSynthesis：播报按钮隐藏；发送按钮不受影响
    expect(wrapper.find('.ai-speak').exists()).toBe(false)
    expect(wrapper.find('.ai-send').exists()).toBe(true)
  })

  it('语音识别（连续模式）：波形随聆听出现，识别结果填入输入框（多段叠加）', async () => {
    let lastRecognizer = null
    class FakeRec {
      constructor() {
        this.lang = ''
        this.interimResults = false
        this.continuous = false
        this.onresult = null
        this.onend = null
        this.onerror = null
        lastRecognizer = this
      }
      start() {}
      stop() {}
    }
    vi.stubGlobal('webkitSpeechRecognition', FakeRec)
    vi.stubGlobal('speechSynthesis', { cancel: vi.fn(), speak: vi.fn(), getVoices: () => [] })

    const wrapper = mountChat()
    await nextTick()

    // 点麦克风进入聆听：波形出现 + 预览为空
    await wrapper.find('.ai-mic').trigger('click')
    expect(wrapper.find('.ai-wave').exists()).toBe(true)

    // 识别结果（isFinal 段，模拟连续识别已确认文本）
    lastRecognizer.onresult({
      results: [{ isFinal: true, 0: { transcript: '查一下订单' } }],
    })
    lastRecognizer.onresult({
      results: [{ isFinal: true, 0: { transcript: '20260801001' } }],
    })
    // 聆听中不触发发送
    expect(chatApi.chatStream).not.toHaveBeenCalled()

    // 再次点击麦克风停止 → 识别文本落框，波形消失
    await wrapper.find('.ai-mic').trigger('click')
    lastRecognizer.onend()
    await nextTick()
    expect(wrapper.find('.ai-wave').exists()).toBe(false)
    expect(wrapper.vm.inputText).toBe('查一下订单 20260801001')
    expect(chatApi.chatStream).not.toHaveBeenCalled()
  })

  it('识别错误：not-allowed 提示权限，聆听态复位', async () => {
    let lastRecognizer = null
    class FakeRec {
      constructor() {
        this.onresult = null
        this.onend = null
        this.onerror = null
        lastRecognizer = this
      }
      start() {}
      stop() {}
    }
    vi.stubGlobal('webkitSpeechRecognition', FakeRec)
    vi.stubGlobal('speechSynthesis', { cancel: vi.fn(), speak: vi.fn(), getVoices: () => [] })
    const { ElMessage } = await import('element-plus')

    const wrapper = mountChat()
    await nextTick()
    await wrapper.find('.ai-mic').trigger('click')
    expect(wrapper.find('.ai-wave').exists()).toBe(true)

    lastRecognizer.onerror({ error: 'not-allowed' })
    await nextTick()

    expect(wrapper.find('.ai-wave').exists()).toBe(false)
    expect(ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('麦克风权限'))
  })

  it('播报开关开启时 done 事件朗读答案；新提问打断上一轮播报', async () => {
    const speakFn = vi.fn()
    const cancelFn = vi.fn()
    vi.stubGlobal('speechSynthesis', {
      cancel: cancelFn,
      speak: speakFn,
      getVoices: () => [],
    })
    vi.stubGlobal(
      'SpeechSynthesisUtterance',
      class FakeUtterance {
        constructor(text) {
          this.text = text
          this.lang = ''
          this.voice = null
        }
      }
    )
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'chat' })
      onEvent('done', { answer: '您的订单已发货。' })
      onDone({ answer: '您的订单已发货。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await nextTick()
    wrapper.vm.speakEnabled = true
    await nextTick()

    await typeAndSend(wrapper, '查订单')

    // done 后朗读答案（经 SpeechSynthesisUtterance.speak）
    expect(speakFn).toHaveBeenCalledTimes(1)
    // onSend 先 stopSpeaking 打断上一轮
    expect(cancelFn).toHaveBeenCalled()
  })

  // ---------- 滚动跟随（P1-4：贴底才跟随） ----------

  // jsdom 无真实滚动几何：defineProperty 伪造列表元素的 scrollHeight/clientHeight，
  // scrollTop 用可写存储（scrollToBottom 的赋值可回读断言）
  function mockListScroll(wrapper, { scrollHeight, clientHeight }) {
    const el = wrapper.find('.chat-messages').element
    Object.defineProperty(el, 'scrollHeight', { configurable: true, value: scrollHeight })
    Object.defineProperty(el, 'clientHeight', { configurable: true, value: clientHeight })
    let top = 0
    Object.defineProperty(el, 'scrollTop', {
      configurable: true,
      get: () => top,
      set: (v) => {
        top = v
      },
    })
    return el
  }

  describe('Chat 滚动跟随（P1-4 贴底才跟随）', () => {
    // 手控流式时间线：emit 注入事件，finish 结束本轮（触发 done 收尾）；
    // pending Promise 让流式期间的操作（上翻/回滚）可以插在 delta 之间
    let emit
    let finish

    function pendingStream() {
      let settled = false
      chatApi.chatStream.mockImplementation((q, opts) => {
        return new Promise((resolve) => {
          emit = (name, data) => {
            if (!settled) opts.onEvent(name, data)
          }
          finish = (answer) => {
            if (settled) return
            settled = true
            opts.onEvent('done', { answer })
            opts.onDone({ answer })
            resolve()
          }
        })
      })
    }

    afterEach(async () => {
      // 防御：用例中途断言失败时流未收尾，兜底结束以防 liveTimer 轮询泄漏
      if (finish) finish('兜底收尾')
      await flushPromises()
      emit = null
      finish = null
    })

    it('发送新消息无条件回底（即使此前处于上翻状态）', async () => {
      pendingStream()
      const wrapper = mountChat()
      await flushPromises()
      const el = mockListScroll(wrapper, { scrollHeight: 2000, clientHeight: 500 })

      // 上翻制造「未贴底」（距底 1400px ≥ 40）
      el.scrollTop = 100
      await wrapper.find('.chat-messages').trigger('scroll')

      await typeAndSend(wrapper, '再问一句')
      expect(el.scrollTop).toBe(2000)

      finish('回答')
      await flushPromises()
    })

    it('贴底时 delta 跟随；上翻后不再拽回；滚回底部恢复跟随', async () => {
      pendingStream()
      const wrapper = mountChat()
      await flushPromises()
      const el = mockListScroll(wrapper, { scrollHeight: 2000, clientHeight: 500 })

      await typeAndSend(wrapper, '华为 Mate 70 Pro 多少钱？')
      expect(el.scrollTop).toBe(2000) // 发送即回底

      emit('start', { query: '华为 Mate 70 Pro 多少钱？', intent: 'faq' })
      emit('delta', { delta: '第一段回答' })
      await flushPromises()

      // 流式中上翻：距底 1400px，后续 delta 不再拽回底部
      el.scrollTop = 100
      await wrapper.find('.chat-messages').trigger('scroll')
      emit('delta', { delta: '第二段回答' })
      await flushPromises()
      expect(el.scrollTop).toBe(100)

      // 滚回底部附近（距底 30px < 40）：恢复跟随
      el.scrollTop = 1470
      await wrapper.find('.chat-messages').trigger('scroll')
      emit('delta', { delta: '第三段回答' })
      await flushPromises()
      expect(el.scrollTop).toBe(2000)

      finish('完整回答')
      await flushPromises()
    })

    it('距底恰好 40px 判为离开底部（<40 才跟随的边界）', async () => {
      pendingStream()
      const wrapper = mountChat()
      await flushPromises()
      const el = mockListScroll(wrapper, { scrollHeight: 2000, clientHeight: 500 })

      await typeAndSend(wrapper, '问一句')
      // 距底 = 2000 - 1460 - 500 = 40，不满足 <40
      el.scrollTop = 1460
      await wrapper.find('.chat-messages').trigger('scroll')
      emit('delta', { delta: '增量内容' })
      await flushPromises()
      expect(el.scrollTop).toBe(1460)

      finish('回答')
      await flushPromises()
    })
  })
})
