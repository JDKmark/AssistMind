import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { computed, provide, inject } from 'vue'

// ---------- mock 依赖 ----------

const mallApi = vi.hoisted(() => ({
  listMyOrders: vi.fn(),
}))

vi.mock('@/api/mall', () => mallApi)

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

import Orders from '@/views/Orders/index.vue'

// ---------- 组件测试的 EP 组件 stub ----------
// 复用 Tickets/Admin.spec.js 的 provide/inject 表格 stub 模式：
// el-table 提供 data 行，列通过 inject 逐行渲染默认插槽（对齐真实组件的 row 作用域）；
// expand 列与普通列统一处理——默认插槽拿到 row 后渲染 items 明细。

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
  'el-tag': {
    props: ['type'],
    template: '<span class="el-tag-stub" :data-type="type"><slot /></span>',
  },
  'el-pagination': {
    props: ['total', 'currentPage', 'pageSize', 'layout'],
    template: '<div class="el-pagination-stub">total:{{ total }}</div>',
  },
}

const ORDERS = [
  {
    order_sn: '2026080112340001',
    status: '已发货',
    pay_amount: 129.5,
    logistics_no: 'SF20260801001',
    created_at: '2026-08-01T12:34:00',
    items: [
      { product_id: 'P1001', name: '无线机械键盘', spec: '黑色/87键', price: 99.0, quantity: 1 },
      { product_id: 'P1002', name: '鼠标垫', spec: '大号', price: 30.5, quantity: 1 },
    ],
  },
  {
    order_sn: '2026080213560002',
    status: '待付款',
    pay_amount: 89,
    logistics_no: '',
    created_at: '2026-08-02T13:56:00',
    items: [{ product_id: 'P1003', name: '蓝牙耳机', spec: '白色', price: 89, quantity: 1 }],
  },
]

describe('Orders 组件', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mallApi.listMyOrders.mockResolvedValue({ orders: ORDERS, total: 2 })
    authMock.role = 'user'
  })

  function mountOrders() {
    return mount(Orders, {
      global: {
        components: stubs,
        directives: { loading: {} },
      },
    })
  }

  it('挂载时调用 listMyOrders 加载第一页并渲染订单号与状态', async () => {
    const wrapper = mountOrders()
    await flushPromises()

    expect(mallApi.listMyOrders).toHaveBeenCalledTimes(1)
    expect(mallApi.listMyOrders).toHaveBeenCalledWith({ limit: 10, offset: 0 })
    expect(wrapper.vm.orders).toEqual(ORDERS)
    expect(wrapper.vm.total).toBe(2)
    const text = wrapper.text()
    expect(text).toContain('2026080112340001')
    expect(text).toContain('2026080213560002')
    expect(text).toContain('已发货')
    expect(text).toContain('待付款')
  })

  it('订单行渲染金额/物流单号/时间，items 明细渲染商品名', async () => {
    const wrapper = mountOrders()
    await flushPromises()

    expect(wrapper.vm.orders[0].items.length).toBe(2)
    const text = wrapper.text()
    // 基础列
    expect(text).toContain('¥129.50')
    expect(text).toContain('SF20260801001')
    expect(text).toContain('2026-08-02 13:56')
    // 物流单号为空显示 -
    expect(text).toContain('-')
    // expand 展开列的商品明细（stub 直接渲染 default 插槽）
    expect(text).toContain('无线机械键盘')
    expect(text).toContain('蓝牙耳机')
    expect(text).toContain('黑色/87键')
  })

  it('状态筛选 change 后以新 status 参数重新调用并重置回第一页', async () => {
    const wrapper = mountOrders()
    await flushPromises()

    wrapper.vm.page = 3
    wrapper.vm.filterStatus = '已发货'
    await wrapper.vm.search()

    expect(mallApi.listMyOrders).toHaveBeenCalledTimes(2)
    expect(wrapper.vm.page).toBe(1)
    expect(mallApi.listMyOrders).toHaveBeenLastCalledWith({
      status: '已发货',
      limit: 10,
      offset: 0,
    })
  })

  it('清空状态筛选后调用不带 status 参数', async () => {
    const wrapper = mountOrders()
    await flushPromises()

    wrapper.vm.filterStatus = ''
    await wrapper.vm.search()

    expect(mallApi.listMyOrders).toHaveBeenLastCalledWith({ limit: 10, offset: 0 })
  })

  it('API reject 时 ElMessage.error 且组件不崩', async () => {
    mallApi.listMyOrders.mockRejectedValueOnce(new Error('network error'))
    const wrapper = mountOrders()
    await flushPromises()

    expect(ElMessageMock.error).toHaveBeenCalledWith('我的订单加载失败')
    expect(wrapper.vm.orders).toEqual([])
    expect(wrapper.vm.total).toBe(0)
    expect(wrapper.exists()).toBe(true)
  })

  it('空列表正常渲染空态且不报错', async () => {
    mallApi.listMyOrders.mockResolvedValueOnce({ orders: [], total: 0 })
    const wrapper = mountOrders()
    await flushPromises()

    expect(wrapper.vm.orders).toEqual([])
    expect(wrapper.exists()).toBe(true)
    // 列头仍在，空数据不崩溃
    expect(wrapper.find('[data-label="订单号"]').exists()).toBe(true)
  })
})
