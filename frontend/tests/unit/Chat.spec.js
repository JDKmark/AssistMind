import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

// ---------- mock 依赖 ----------

const chatApi = vi.hoisted(() => ({
  chatStream: vi.fn(),
}))

vi.mock('@/api/chat', () => chatApi)

const feedbackApi = vi.hoisted(() => ({
  submitFeedback: vi.fn(),
}))

vi.mock('@/api/feedback', () => feedbackApi)

vi.mock('element-plus', () => ({ ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))

import Chat from '@/views/Chat/index.vue'

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
  'el-empty': { template: '<div class="el-empty-stub"><slot /></div>' },
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
    chatApi.chatStream.mockResolvedValue()
  })

  function mountChat() {
    return mount(Chat, { global: { components: stubs } })
  }

  function sendButton(wrapper) {
    return wrapper.findAll('.el-button-stub').find((b) => b.text() === '发送')
  }

  async function typeAndSend(wrapper, text) {
    await wrapper.find('.el-input-stub').setValue(text)
    await sendButton(wrapper).trigger('click')
    await flushPromises()
  }

  it('渲染消息列表空态、快捷提问引导条与输入框', async () => {
    const wrapper = mountChat()
    await nextTick()

    const text = wrapper.text()
    expect(text).toContain('华为 Mate 60 Pro 多少钱')
    expect(text).toContain('查一下订单 20240801001')
    expect(text).toContain('我要退货')
    expect(text).toContain('物流到哪了')
    expect(wrapper.find('.el-input-stub').exists()).toBe(true)
    expect(sendButton(wrapper)).toBeTruthy()
    // 空输入时发送按钮禁用
    expect(sendButton(wrapper).attributes('disabled')).toBeDefined()
  })

  it('点击快捷提问填充输入框', async () => {
    const wrapper = mountChat()
    const btn = wrapper
      .findAll('.el-button-stub')
      .find((b) => b.text().includes('我要退货'))
    await btn.trigger('click')
    expect(wrapper.vm.inputText).toBe('我要退货')
  })

  it('发送消息调用 chatStream，携带 query 与空 history 并清空输入框', async () => {
    const wrapper = mountChat()
    await typeAndSend(wrapper, '华为 Mate 60 Pro 多少钱')

    expect(chatApi.chatStream).toHaveBeenCalledTimes(1)
    const [q, opts] = chatApi.chatStream.mock.calls[0]
    expect(q).toBe('华为 Mate 60 Pro 多少钱')
    expect(opts.history).toEqual([])
    expect(wrapper.vm.inputText).toBe('')
  })

  it('Enter 键发送消息', async () => {
    const wrapper = mountChat()
    const input = wrapper.find('.el-input-stub')
    await input.setValue('查一下订单 20240801001')
    await input.trigger('keydown', { key: 'Enter' })

    expect(chatApi.chatStream).toHaveBeenCalledWith(
      '查一下订单 20240801001',
      expect.objectContaining({ history: [] }),
    )
  })

  it('SSE 事件驱动渲染：faq 意图展示回答与知识来源列表', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'faq' })
      onEvent('retrieving', {})
      onEvent('generating', {})
      onEvent('done', {
        answer: '华为 Mate 60 Pro 起售价 **6999 元**',
        sources: [
          { title: '华为 Mate 60 系列价格说明', snippet: '…' },
          { title: 'Mate 60 Pro 商品规格', snippet: '…' },
        ],
      })
      onDone({ answer: '华为 Mate 60 Pro 起售价 **6999 元**' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '华为 Mate 60 Pro 多少钱')

    expect(wrapper.text()).toContain('华为 Mate 60 Pro 起售价')
    expect(wrapper.text()).toContain('知识来源')
    expect(wrapper.text()).toContain('华为 Mate 60 系列价格说明')
    expect(wrapper.text()).toContain('Mate 60 Pro 商品规格')
    // markdown 加粗渲染
    expect(wrapper.find('.md-body strong').exists()).toBe(true)
    // 用户消息也在列表中
    expect(wrapper.find('.bubble.user').text()).toContain('华为 Mate 60 Pro 多少钱')
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
      onEvent('tool_call', { tool_name: 'query_order', arguments: { order_sn: '20240801001' } })
      onEvent('tool_result', {
        tool_name: 'query_order',
        result: {
          order_sn: '20240801001',
          status: '已发货',
          items: [{ product_id: 'P001', name: '华为 Mate 60 Pro', spec: '256G 雅丹黑', price: 6999, quantity: 1 }],
          pay_amount: 6999,
          logistics_no: 'SF1234567890',
          created_at: '2024-08-01 09:30:00',
        },
      })
      onEvent('done', { answer: '您的订单 20240801001 已发货。' })
      onDone({ answer: '您的订单 20240801001 已发货。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '查一下订单 20240801001')

    const text = wrapper.text()
    // 订单卡片渲染（el-descriptions stub）
    expect(wrapper.find('.tool-result-card').exists()).toBe(true)
    expect(text).toContain('订单号')
    expect(text).toContain('20240801001')
    expect(text).toContain('已发货')
    expect(text).toContain('6999')
    expect(text).toContain('SF1234567890')
    expect(text).toContain('华为 Mate 60 Pro')
  })

  it('SSE 工具结果卡片：query_logistics 渲染物流轨迹卡片', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'task' })
      onEvent('tool_call', { tool_name: 'query_logistics', arguments: { order_sn: '20240801001' } })
      onEvent('tool_result', {
        tool_name: 'query_logistics',
        result: [
          { ts: '2024-08-01 16:00:00', content: '已揽收' },
          { ts: '2024-08-01 18:30:00', content: '运输中（预计明天送达）' },
        ],
      })
      onEvent('done', { answer: '您的订单 20240801001 物流轨迹：已揽收 → 运输中。' })
      onDone({ answer: '您的订单 20240801001 物流轨迹：已揽收 → 运输中。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '物流到哪了')

    const text = wrapper.text()
    // 物流轨迹卡片（el-timeline stub 渲染 ts + content）
    expect(wrapper.find('.el-timeline-stub').exists()).toBe(true)
    expect(text).toContain('2024-08-01 16:00:00')
    expect(text).toContain('已揽收')
    expect(text).toContain('2024-08-01 18:30:00')
    expect(text).toContain('运输中（预计明天送达）')
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

  it('流式请求进行中禁用发送按钮', async () => {
    let resolveStream
    chatApi.chatStream.mockImplementation(
      () => new Promise((resolve) => { resolveStream = resolve }),
    )

    const wrapper = mountChat()
    await wrapper.find('.el-input-stub').setValue('测试问题')
    await sendButton(wrapper).trigger('click')
    expect(sendButton(wrapper).attributes('disabled')).toBeDefined()

    resolveStream()
    await flushPromises()
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

    // 完成后渲染反馈条（有帮助 / 没帮助 / 评论 / 提交）
    const helpful = wrapper.findAll('.el-button-stub').find((b) => b.text() === '有帮助')
    expect(helpful).toBeTruthy()

    await helpful.trigger('click') // 有帮助 → score 5
    await wrapper.find('.feedback-comment').setValue('很好')
    const submitBtn = wrapper.findAll('.el-button-stub').find((b) => b.text() === '提交')
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

  it('未打分时不提交反馈', async () => {
    feedbackApi.submitFeedback.mockResolvedValue({ feedback_id: 'fb-1', created: true })
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'faq', conversation_id: 'conv-2' })
      onEvent('done', { answer: '这没有问题', conversation_id: 'conv-2' })
      onDone({ answer: '这没有问题' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '你好')

    const submitBtn = wrapper.findAll('.el-button-stub').find((b) => b.text() === '提交')
    // 未打分时提交按钮禁用
    expect(submitBtn.attributes('disabled')).toBeDefined()
    await submitBtn.trigger('click')
    expect(feedbackApi.submitFeedback).not.toHaveBeenCalled()
  })

  it('来源列表展示命中分数，并可展开溯因片段全文', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'faq', conversation_id: 'conv-3' })
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
        degraded: [],
        conversation_id: 'conv-3',
      })
      onDone({ answer: '48 小时内。' })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '退货多久到账？')
    await nextTick()

    // 分数展示
    expect(wrapper.text()).toContain('0.92')

    // 展开溯因：片段全文出现
    const toggle = wrapper.findAll('.el-button-stub').find((b) => b.text() === '查看溯因')
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
})
