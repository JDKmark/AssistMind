import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick, computed, provide, inject } from 'vue'

// ---------- mock 依赖 ----------

const healthApi = vi.hoisted(() => ({
  getHealth: vi.fn(),
}))

const knowledgeApi = vi.hoisted(() => ({
  listDocs: vi.fn(),
}))

const ticketApi = vi.hoisted(() => ({
  listTickets: vi.fn(),
}))

const feedbackApi = vi.hoisted(() => ({
  listFeedback: vi.fn(),
}))

const mallApi = vi.hoisted(() => ({
  listOrders: vi.fn(),
  listRefunds: vi.fn(),
  updateRefundStatus: vi.fn(),
}))

vi.mock('@/api/health', () => healthApi)
vi.mock('@/api/knowledge', () => knowledgeApi)
vi.mock('@/api/ticket', () => ticketApi)
vi.mock('@/api/feedback', () => feedbackApi)
vi.mock('@/api/mall', () => mallApi)

const ElMessageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('element-plus', () => ({ ElMessage: ElMessageMock }))

import Admin from '@/views/Admin/index.vue'

// ---------- 组件测试的 EP 组件 stub ----------

const stubs = {
  'el-row': { template: '<div class="el-row-stub"><slot /></div>' },
  'el-col': { template: '<div class="el-col-stub"><slot /></div>' },
  'el-card': {
    template:
      '<div class="el-card-stub"><div class="el-card-header"><slot name="header" /></div><div class="el-card-body"><slot /></div></div>',
  },
  'el-tag': {
    props: ['type'],
    template: '<span class="el-tag-stub" :data-type="type"><slot /></span>',
  },
  // el-table 提供 data 行，列通过 inject 逐行渲染默认插槽（对齐真实组件的 row 作用域）
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
    template:
      '<div class="el-table-column-stub"><div v-for="(r, i) in rows" :key="i" class="el-table-row-stub"><slot :row="r" /></div></div>',
  },
  'el-descriptions': { template: '<div class="el-descriptions-stub"><slot /></div>' },
  'el-descriptions-item': {
    props: ['label'],
    template:
      '<div class="el-descriptions-item-stub"><span class="el-descriptions-label">{{ label }}</span><slot /></div>',
  },
  'el-select': {
    props: ['modelValue'],
    emits: ['update:modelValue', 'change'],
    template: '<div class="el-select-stub"><slot /></div>',
  },
  'el-option': { template: '<span class="el-option-stub" />' },
  'el-input': {
    props: ['modelValue', 'placeholder', 'size'],
    emits: ['update:modelValue', 'clear'],
    template: '<input class="el-input-stub" :placeholder="placeholder" />',
  },
  'el-pagination': {
    props: ['total', 'currentPage', 'pageSize', 'layout'],
    template: '<div class="el-pagination-stub">total:{{ total }}</div>',
  },
  'el-button': {
    props: ['disabled', 'loading'],
    emits: ['click'],
    template:
      '<button class="el-button-stub" :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
  },
  'el-drawer': {
    props: ['modelValue'],
    template: '<div class="el-drawer-stub" v-if="modelValue"><slot /></div>',
  },
}

describe('Admin 组件', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    healthApi.getHealth.mockResolvedValue({
      status: 'ok',
      dependencies: {
        qdrant: 'ok',
        redis: 'ok',
        postgres: 'pending',
        langfuse: 'disabled',
      },
    })
    knowledgeApi.listDocs.mockResolvedValue({
      docs: [
        { doc_id: 'ops-1', title: '运维手册', category: 'ops', chunk_count: 2 },
        { doc_id: 'mall-1', title: '商城文档', category: 'mall', chunk_count: 3 },
      ],
      total: 2,
    })
    ticketApi.listTickets.mockResolvedValue({
      tickets: [
        { id: 'TK-1', status: 'open' },
        { id: 'TK-2', status: 'open' },
        { id: 'TK-3', status: 'resolved' },
      ],
      total: 3,
    })
    feedbackApi.listFeedback.mockResolvedValue({
      total: 1,
      items: [
        {
          id: 'f-1',
          score: 2,
          query: '退货多久到账？',
          answer: '48 小时内。',
          sources: [
            {
              title: '退货规则',
              source: 'knowledge/mall/business/returns.md',
              score: 0.92,
              text: '退货款项在申请通过后 48 小时内原路退回。',
            },
          ],
          intent: 'faq',
          crag_action: 'rewrite_retry',
          degraded: ['reranker'],
          trace_id: 'trace-abc',
          created_at: '2026-08-21T10:00:00',
          exported: false,
        },
      ],
      langfuse_host: 'http://localhost:3001',
    })
    mallApi.listRefunds.mockResolvedValue({
      refunds: [],
      total: 0,
    })
    adminApi.getOverview.mockResolvedValue({ users: { total: 0 }, degraded: [] })
    adminApi.listUsers.mockResolvedValue({ users: [], total: 0 })
    adminApi.listAuditLogs.mockResolvedValue({ items: [], total: 0 })
    mallApi.listOrders.mockResolvedValue({
      orders: [
        {
          order_sn: '2026080112340001',
          owner_username: 'user1',
          status: '已发货',
          pay_amount: 129.5,
          logistics_no: 'SF20260801001',
          created_at: '2026-08-01T12:34:00',
        },
        {
          order_sn: '2026080213560002',
          owner_username: 'user2',
          status: '待付款',
          pay_amount: 89,
          logistics_no: '',
          created_at: '2026-08-02T13:56:00',
        },
      ],
      total: 2,
    })
  })

  function mountAdmin() {
    return mount(Admin, { global: { components: stubs } })
  }

  it('渲染系统状态与数据概览，并加载健康/知识库/工单数据', async () => {
    const wrapper = mount(Admin, { global: { components: stubs } })
    await flushPromises()

    expect(wrapper.text()).toContain('系统状态')
    expect(wrapper.text()).toContain('数据概览')
    // 组件行渲染（el-table 列由真实组件渲染，这里断言 vm 状态）
    const labels = wrapper.vm.depRows.map((r) => r.label)
    expect(labels).toEqual(['Qdrant', 'Redis', 'PostgreSQL', 'Langfuse'])
    expect(healthApi.getHealth).toHaveBeenCalledTimes(1)
    expect(knowledgeApi.listDocs).toHaveBeenCalledTimes(1)
    expect(ticketApi.listTickets).toHaveBeenCalledWith(null, 200)
  })

  it('健康状态映射：ok 绿色 / pending 黄色 / disabled 灰色', async () => {
    const wrapper = mountAdmin()
    await flushPromises()

    const rows = wrapper.vm.depRows
    expect(rows.find((r) => r.key === 'qdrant').tagType).toBe('success')
    expect(rows.find((r) => r.key === 'postgres').tagType).toBe('warning')
    expect(rows.find((r) => r.key === 'langfuse').tagType).toBe('info')

    // 有 pending 时整体标签为黄色「部分待定」
    expect(wrapper.vm.overallLabel).toBe('部分待定')
    expect(wrapper.vm.overallTagType).toBe('warning')
    expect(wrapper.find('[data-type="warning"]').exists()).toBe(true)
  })

  it('全部依赖正常时整体标签为绿色「正常」', async () => {
    healthApi.getHealth.mockResolvedValue({
      status: 'ok',
      dependencies: { qdrant: 'ok', redis: 'ok', postgres: 'ok', langfuse: 'disabled' },
    })
    const wrapper = mountAdmin()
    await flushPromises()

    expect(wrapper.vm.overallLabel).toBe('正常')
    expect(wrapper.vm.overallTagType).toBe('success')
    expect(wrapper.find('[data-type="success"]').exists()).toBe(true)
  })

  it('数据概览：知识库统计与工单状态分布', async () => {
    const wrapper = mountAdmin()
    await flushPromises()

    expect(wrapper.vm.kbTotal).toBe(2)
    expect(wrapper.vm.kbChunks).toBe(5)
    expect(wrapper.vm.ticketTotal).toBe(3)
    expect(wrapper.vm.statusDist).toEqual({
      open: 2,
      in_progress: 0,
      resolved: 1,
      closed: 0,
      other: 0,
    })
    expect(wrapper.text()).toContain('文档数')
    expect(wrapper.text()).toContain('Chunk 数')
    expect(wrapper.text()).toContain('待处理')
    expect(wrapper.text()).toContain('已解决')
  })

  it('知识库不可用时展示错误提示', async () => {
    knowledgeApi.listDocs.mockResolvedValue({
      docs: [],
      total: 0,
      error: 'Qdrant 不可用，知识库列表暂不可用',
    })
    const wrapper = mountAdmin()
    await flushPromises()

    expect(wrapper.vm.kbError).toContain('Qdrant')
    expect(wrapper.text()).toContain('Qdrant 不可用')
  })

  // ---------- 反馈与追溯 ----------

  it('加载反馈列表并渲染差评行状态', async () => {
    const wrapper = mountAdmin()
    await flushPromises()

    expect(feedbackApi.listFeedback).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.feedbacks.length).toBe(1)
    expect(wrapper.vm.feedbacks[0].query).toBe('退货多久到账？')
    expect(wrapper.vm.langfuseHost).toBe('http://localhost:3001')
    expect(wrapper.text()).toContain('反馈与追溯')
  })

  it('评分/回流筛选参数透传到 listFeedback', async () => {
    const wrapper = mountAdmin()
    await flushPromises()

    wrapper.vm.fbScoreFilter = 2
    wrapper.vm.fbExportedFilter = 'pending'
    await wrapper.vm.loadFeedback()

    expect(feedbackApi.listFeedback).toHaveBeenCalledTimes(2)
    const params = feedbackApi.listFeedback.mock.calls[1][0]
    expect(params.score).toBe(2)
    expect(params.exported).toBe(false)
  })

  it('点击追溯打开抽屉：时间线展示来源/CRAG 决策/降级项与 Langfuse 链接', async () => {
    const wrapper = mountAdmin()
    await flushPromises()

    // 打开抽屉
    wrapper.vm.openTrace(wrapper.vm.feedbacks[0])
    await nextTick()

    expect(wrapper.vm.traceDrawer).toBe(true)
    expect(wrapper.vm.currentTrace.id).toBe('f-1')
    expect(wrapper.vm.cragLabel).toBe('被动改写重试')
    expect(wrapper.vm.langfuseUrl('trace-abc')).toBe('http://localhost:3001/trace/trace-abc')

    const text = wrapper.text()
    // 时间线内容（el-drawer stub v-if modelValue 渲染）
    expect(text).toContain('退货多久到账？')
    expect(text).toContain('退货规则')
    expect(text).toContain('0.92')
    expect(text).toContain('48 小时内。')
    expect(text).toContain('降级: reranker')
    expect(text).toContain('在 Langfuse 查看完整 trace')
  })

  // ---------- 商城订单 ----------

  it('挂载时拉取商城订单第一页并渲染订单号与归属用户', async () => {
    const wrapper = mountAdmin()
    await flushPromises()

    expect(mallApi.listOrders).toHaveBeenCalledTimes(1)
    expect(mallApi.listOrders).toHaveBeenCalledWith({ limit: 10, offset: 0 })
    expect(wrapper.vm.orders.length).toBe(2)
    const text = wrapper.text()
    expect(text).toContain('商城订单')
    expect(text).toContain('2026080112340001')
    expect(text).toContain('2026080213560002')
    expect(text).toContain('user1')
    expect(text).toContain('user2')
    expect(wrapper.vm.orderTotal).toBe(2)
  })

  it('订单状态映射 el-tag 类型：待付款黄 / 已发货蓝 / 已完成绿', async () => {
    const wrapper = mountAdmin()
    await flushPromises()

    expect(wrapper.vm.orderStatusTagType('待付款')).toBe('warning')
    expect(wrapper.vm.orderStatusTagType('待发货')).toBe('info')
    expect(wrapper.vm.orderStatusTagType('已发货')).toBe('primary')
    expect(wrapper.vm.orderStatusTagType('已完成')).toBe('success')
  })

  it('用户/状态筛选透传 listOrders 且重置回第一页', async () => {
    const wrapper = mountAdmin()
    await flushPromises()

    wrapper.vm.orderPage = 3
    wrapper.vm.orderOwnerFilter = 'user1'
    wrapper.vm.orderStatusFilter = '已发货'
    await wrapper.vm.searchOrders()

    expect(mallApi.listOrders).toHaveBeenCalledTimes(2)
    expect(wrapper.vm.orderPage).toBe(1)
    expect(mallApi.listOrders).toHaveBeenLastCalledWith({
      limit: 10,
      offset: 0,
      owner_username: 'user1',
      status: '已发货',
    })
  })

  it('订单加载失败时 ElMessage.error 且不阻塞其它卡片数据', async () => {
    mallApi.listOrders.mockRejectedValueOnce(new Error('network error'))
    const wrapper = mountAdmin()
    await flushPromises()

    expect(ElMessageMock.error).toHaveBeenCalledWith('商城订单加载失败')
    expect(wrapper.vm.orders).toEqual([])
    // 其它卡片仍正常加载
    expect(healthApi.getHealth).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.ticketTotal).toBe(3)
    expect(wrapper.vm.feedbacks.length).toBe(1)
  })
})

const adminApi = vi.hoisted(() => ({
  getOverview: vi.fn(),
  listUsers: vi.fn(),
  updateUser: vi.fn(),
  listAuditLogs: vi.fn(),
}))

vi.mock('@/api/admin', () => adminApi)


describe('Admin phase12 运营操作', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    healthApi.getHealth.mockResolvedValue({ status: 'ok', dependencies: {} })
    knowledgeApi.listDocs.mockResolvedValue({ docs: [], total: 0 })
    ticketApi.listTickets.mockResolvedValue({ tickets: [], total: 0 })
    feedbackApi.listFeedback.mockResolvedValue({ items: [], total: 0 })
    mallApi.listOrders.mockResolvedValue({ orders: [], total: 0 })
    mallApi.listRefunds.mockResolvedValue({ refunds: [], total: 0 })
  })

  it('加载统一概览、用户和审计数据', async () => {
    adminApi.getOverview.mockResolvedValue({
      orders: { total: 1, amount: 10, by_status: {} },
      tickets: { total: 1, by_status: { open: 1 } },
      refunds: { total: 0, by_status: {} },
      users: { total: 1, by_role: { user: 1 } },
      feedback: { total: 0, negative: 0, pending_export: 0 },
      knowledge: { documents: 1, chunks: 2 },
      degraded: [],
    })
    adminApi.listUsers.mockResolvedValue({ users: [{ id: 'u1', username: 'alice', role: 'user', is_active: true }], total: 1 })
    adminApi.listAuditLogs.mockResolvedValue({ items: [], total: 0 })

    const wrapper = mount(Admin, { global: { components: stubs } })
    await flushPromises()

    expect(adminApi.getOverview).toHaveBeenCalled()
    expect(adminApi.listUsers).toHaveBeenCalled()
    expect(adminApi.listAuditLogs).toHaveBeenCalled()
    expect(wrapper.vm.adminOverview.users.total).toBe(1)
  })

  it('调整用户角色后刷新用户、概览和审计', async () => {
    adminApi.getOverview.mockResolvedValue({ users: { total: 1 }, degraded: [] })
    adminApi.listUsers.mockResolvedValue({ users: [], total: 0 })
    adminApi.listAuditLogs.mockResolvedValue({ items: [], total: 0 })
    adminApi.updateUser.mockResolvedValue({ id: 'u1', username: 'alice', role: 'agent', is_active: true })

    const wrapper = mount(Admin, { global: { components: stubs } })
    await flushPromises()
    await wrapper.vm.changeUserRole({ id: 'u1', role: 'user' }, 'agent')

    expect(adminApi.updateUser).toHaveBeenCalledWith('u1', { role: 'agent' })
    expect(adminApi.listUsers).toHaveBeenCalledTimes(2)
    expect(adminApi.getOverview).toHaveBeenCalledTimes(3)
    expect(adminApi.listAuditLogs).toHaveBeenCalledTimes(2)
  })

  it('启停用户后刷新用户、概览和审计', async () => {
    adminApi.getOverview.mockResolvedValue({ users: { total: 1 }, degraded: [] })
    adminApi.listUsers.mockResolvedValue({ users: [], total: 0 })
    adminApi.listAuditLogs.mockResolvedValue({ items: [], total: 0 })
    adminApi.updateUser.mockResolvedValue({ id: 'u1', is_active: false })
    const wrapper = mount(Admin, { global: { components: stubs } })
    await flushPromises()

    await wrapper.vm.toggleUser({ id: 'u1', is_active: true })

    expect(adminApi.updateUser).toHaveBeenCalledWith('u1', { is_active: false })
  })

  it('工单筛选透传状态、优先级和客户', async () => {
    adminApi.getOverview.mockResolvedValue({ tickets: { total: 0 }, degraded: [] })
    adminApi.listUsers.mockResolvedValue({ users: [], total: 0 })
    adminApi.listAuditLogs.mockResolvedValue({ items: [], total: 0 })
    const wrapper = mount(Admin, { global: { components: stubs } })
    await flushPromises()
    ticketApi.listTickets.mockClear()
    wrapper.vm.ticketStatusFilter = 'open'
    wrapper.vm.ticketPriorityFilter = 'urgent'
    wrapper.vm.ticketCustomerFilter = 'alice'

    await wrapper.vm.searchTickets()

    expect(ticketApi.listTickets).toHaveBeenCalledWith('open', 50, {
      priority: 'urgent',
      user_id: 'alice',
    })
  })
})
