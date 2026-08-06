import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

// ---------- mock 依赖 ----------

const chatApi = vi.hoisted(() => ({
  chatStream: vi.fn(),
}))

vi.mock('@/api/chat', () => chatApi)

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
    expect(wrapper.text()).toContain('已创建售后工单')
    expect(wrapper.text()).toContain('工单列表')
    expect(wrapper.find('.router-link-stub').exists()).toBe(true)
  })

  it('SSE 事件驱动渲染：diagnose 意图展示诊断报告提示与故障工单', async () => {
    chatApi.chatStream.mockImplementation((q, { onEvent, onDone }) => {
      onEvent('start', { query: q, intent: 'diagnose' })
      onEvent('planning', { plan: {} })
      onEvent('collecting', {})
      onEvent('evidence', { kb: [{ title: '订单超时排查手册' }] })
      onEvent('analyzing', {})
      onEvent('done', {
        report: {
          summary: '数据库连接池耗尽',
          root_cause: '连接数过小',
          ticket_id: 'TK-20260805002',
        },
      })
      onDone({ report: { summary: '数据库连接池耗尽' } })
      return Promise.resolve()
    })

    const wrapper = mountChat()
    await typeAndSend(wrapper, '系统登录超时')

    expect(wrapper.text()).toContain('数据库连接池耗尽')
    expect(wrapper.text()).toContain('已生成诊断报告')
    expect(wrapper.text()).toContain('运维诊断页')
    expect(wrapper.text()).toContain('TK-20260805002')
    expect(wrapper.text()).toContain('已创建故障工单')
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
})
