import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { computed, provide, inject } from 'vue'

// ---------- mock 依赖 ----------

const ticketApi = vi.hoisted(() => ({
  listTickets: vi.fn(),
  createTicket: vi.fn(),
  updateTicketStatus: vi.fn(),
  getTicket: vi.fn(),
}))

vi.mock('@/api/ticket', () => ticketApi)

const ElMessageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('element-plus', () => ({ ElMessage: ElMessageMock }))

const authMock = vi.hoisted(() => ({ role: 'user' }))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => authMock,
}))

import Tickets from '@/views/Tickets/index.vue'

// ---------- 组件测试的 EP 组件 stub ----------

// el-table 提供 data 行，列通过 inject 逐行渲染默认插槽（对齐真实组件的 row 作用域）；
// 列头通过 data-label 暴露给断言；无默认插槽的 prop 列走 slot fallback 渲染行字段值。

const stubs = {
  'el-table': {
    props: ['data'],
    setup(props) {
      provide('amTableRows', computed(() => props.data || []))
    },
    template: '<div class="el-table-stub"><slot /></div>',
  },
  'el-table-column': {
    props: ['label', 'prop'],
    setup() {
      const rows = inject('amTableRows', [])
      return { rows }
    },
    template: [
      '<div class="el-table-column-stub" :data-label="label">',
      '<div class="el-table-header-stub">{{ label }}</div>',
      '<div v-for="(r, i) in rows" :key="i" class="el-table-row-stub">',
      '<slot :row="r">{{ prop ? r[prop] : "" }}</slot>',
      '</div>',
      '</div>',
    ].join(''),
  },
  'el-select': {
    props: ['modelValue', 'size'],
    emits: ['update:modelValue', 'change'],
    template: '<div class="el-select-stub"><slot /></div>',
  },
  'el-option': { template: '<span class="el-option-stub" />' },
  'el-button': {
    props: ['disabled', 'loading'],
    emits: ['click'],
    template:
      '<button class="el-button-stub" :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
  },
  'el-tag': {
    props: ['type'],
    template: '<span class="el-tag-stub" :data-type="type"><slot /></span>',
  },
  'el-dialog': {
    props: ['modelValue', 'title'],
    template: '<div class="el-dialog-stub" v-if="modelValue"><slot /></div>',
  },
  'el-descriptions': { template: '<div class="el-descriptions-stub"><slot /></div>' },
  'el-descriptions-item': {
    props: ['label'],
    template:
      '<div class="el-descriptions-item-stub"><span class="el-descriptions-label">{{ label }}</span><slot /></div>',
  },
  'el-form': { props: ['model'], template: '<form class="el-form-stub"><slot /></form>' },
  'el-form-item': { template: '<div class="el-form-item-stub"><slot /></div>' },
  'el-input': {
    props: ['modelValue', 'placeholder'],
    emits: ['update:modelValue'],
    template: '<input class="el-input-stub" :placeholder="placeholder" />',
  },
}

const TICKETS = [
  {
    id: 'TK-20260820001',
    title: '无法登录',
    status: 'open',
    priority: 'high',
    user_id: 'alice',
    created_at: '2026-08-20T10:00:00',
  },
  {
    id: 'TK-20260821002',
    title: '退款进度咨询',
    status: 'in_progress',
    priority: 'normal',
    user_id: 'bob',
    created_at: '2026-08-21T11:30:00',
  },
  {
    id: 'TK-20260822003',
    title: '支付失败',
    status: 'resolved',
    priority: 'urgent',
    user_id: 'carol',
    created_at: '2026-08-22T09:00:00',
  },
]

const TICKET_DETAIL = {
  id: 'TK-20260820001',
  title: '无法登录',
  description: '从昨天下午开始，APP 和网页端都无法登录，提示 token 过期，重置密码后仍然无效，请尽快协助处理。',
  priority: 'high',
  status: 'open',
  category: 'account',
  user_id: 'alice',
  created_at: '2026-08-20T10:00:00',
  updated_at: '2026-08-20T12:30:00',
}

describe('Tickets 组件', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ticketApi.listTickets.mockResolvedValue({ tickets: TICKETS })
    ticketApi.getTicket.mockResolvedValue(TICKET_DETAIL)
    ticketApi.updateTicketStatus.mockResolvedValue({})
    authMock.role = 'user'
  })

  function mountTickets() {
    return mount(Tickets, {
      global: {
        components: stubs,
        directives: { loading: {} },
      },
    })
  }

  it('挂载时加载工单列表并渲染基础列', async () => {
    const wrapper = mountTickets()
    await flushPromises()

    expect(ticketApi.listTickets).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.tickets).toEqual(TICKETS)
    expect(wrapper.find('[data-label="工单ID"]').exists()).toBe(true)
    expect(wrapper.find('[data-label="标题"]').exists()).toBe(true)
    expect(wrapper.find('[data-label="状态"]').exists()).toBe(true)
  })

  it('user 视角：显示操作列（详情按钮）但隐藏客户列与状态下拉', async () => {
    const wrapper = mountTickets()
    await flushPromises()

    expect(wrapper.find('[data-label="操作"]').exists()).toBe(true)
    expect(wrapper.find('[data-label="客户"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('客户')
    // 所有行都有详情按钮
    const detailBtns = wrapper.findAll('button').filter((b) => b.text() === '详情')
    expect(detailBtns.length).toBe(TICKETS.length)
  })

  it('agent 视角：显示操作列与客户列，行渲染客户 user_id', async () => {
    authMock.role = 'agent'
    const wrapper = mountTickets()
    await flushPromises()

    expect(wrapper.find('[data-label="操作"]').exists()).toBe(true)
    const customerCol = wrapper.find('[data-label="客户"]')
    expect(customerCol.exists()).toBe(true)
    expect(customerCol.text()).toContain('alice')
    expect(customerCol.text()).toContain('bob')
  })

  it('admin 视角：同 agent 显示操作列与客户列', async () => {
    authMock.role = 'admin'
    const wrapper = mountTickets()
    await flushPromises()

    expect(wrapper.find('[data-label="操作"]').exists()).toBe(true)
    expect(wrapper.find('[data-label="客户"]').exists()).toBe(true)
    expect(wrapper.find('[data-label="客户"]').text()).toContain('alice')
  })

  // ---------- 工单详情 ----------

  it('点击详情按钮 → getTicket 被调用 → 弹窗渲染完整描述文本', async () => {
    const wrapper = mountTickets()
    await flushPromises()

    const detailBtn = wrapper.findAll('button').filter((b) => b.text() === '详情')[0]
    await detailBtn.trigger('click')
    await flushPromises()

    expect(ticketApi.getTicket).toHaveBeenCalledWith(TICKETS[0].id)
    expect(wrapper.vm.detailVisible).toBe(true)
    expect(wrapper.vm.currentTicket).toEqual(TICKET_DETAIL)
    // 弹窗渲染完整描述（stub v-if modelValue 渲染 slot）
    expect(wrapper.text()).toContain(TICKET_DETAIL.description)
    expect(wrapper.text()).toContain('标题')
    expect(wrapper.text()).toContain('优先级')
    expect(wrapper.text()).toContain('更新时间')
  })

  it('user 视角：resolved 行有确认解决按钮，open 行没有', async () => {
    const wrapper = mountTickets()
    await flushPromises()

    const opRows = wrapper.find('[data-label="操作"]').findAll('.el-table-row-stub')
    expect(opRows.length).toBe(TICKETS.length)
    // open 行：仅详情按钮
    expect(opRows[0].text()).toContain('详情')
    expect(opRows[0].text()).not.toContain('确认解决')
    // resolved 行：详情 + 确认解决
    expect(opRows[2].text()).toContain('确认解决')
  })

  it('点击确认解决 → updateTicketStatus 以 (id, closed) 调用并刷新列表', async () => {
    const wrapper = mountTickets()
    await flushPromises()

    const confirmBtn = wrapper.findAll('button').filter((b) => b.text() === '确认解决')[0]
    await confirmBtn.trigger('click')
    await flushPromises()

    expect(ticketApi.updateTicketStatus).toHaveBeenCalledWith('TK-20260822003', 'closed')
    expect(ElMessageMock.success).toHaveBeenCalled()
    // 成功后重新加载列表
    expect(ticketApi.listTickets).toHaveBeenCalledTimes(2)
  })

  it('agent 视角：操作列保留状态下拉与详情按钮并列，无确认解决按钮', async () => {
    authMock.role = 'agent'
    const wrapper = mountTickets()
    await flushPromises()

    const opCol = wrapper.find('[data-label="操作"]')
    expect(opCol.exists()).toBe(true)
    expect(opCol.findAll('.el-select-stub').length).toBe(TICKETS.length)
    expect(wrapper.findAll('button').filter((b) => b.text() === '详情').length).toBe(TICKETS.length)
    expect(wrapper.text()).not.toContain('确认解决')
  })
})
