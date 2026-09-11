import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick, reactive, ref } from 'vue'

// ---------- mock 依赖 ----------

// ticketPolling：ticketUnread 为可写 ref（测试中改值断言徽标），轮询函数为 spy
vi.mock('@/utils/ticketPolling', () => ({
  ticketUnread: ref(0),
  startTicketPolling: vi.fn(),
  stopTicketPolling: vi.fn(),
  resetTicketPolling: vi.fn(),
}))

import {
  ticketUnread,
  startTicketPolling,
  stopTicketPolling,
  resetTicketPolling,
} from '@/utils/ticketPolling'

// auth store 用 reactive：组件内 watch(() => auth.token) 才能触发
const authMock = vi.hoisted(() => {
  // vitest ESM 下从 vue 取 reactive（hoisted 阶段可安全 import）
  return null
})

vi.mock('@/stores/auth', async () => {
  const { reactive } = await import('vue')
  const auth = reactive({ token: 'tok-1', role: 'user', user: { username: 'user1' } })
  return { useAuthStore: () => auth }
})

// route 用 reactive：组件内 watch(() => route.path)（路由跳转收起抽屉）才能被测试触发
const routerState = vi.hoisted(() => ({}))

vi.mock('vue-router', async () => {
  const { reactive } = await import('vue')
  const route = reactive({ path: '/chat', meta: { title: '智能问答' } })
  routerState.route = route
  return {
    useRoute: () => route,
    useRouter: () => ({ push: vi.fn(), getRoutes: () => [] }),
  }
})

import MainLayout from '@/components/Layout/MainLayout.vue'

function mountLayout() {
  return mount(MainLayout, { global: { components: stubs } })
}

// ---------- EP 组件 stub ----------

const stubs = {
  'el-container': { template: '<div class="el-container-stub"><slot /></div>' },
  'el-aside': { template: '<div class="el-aside-stub"><slot /></div>' },
  'el-header': { template: '<div class="el-header-stub"><slot /></div>' },
  'el-main': { template: '<div class="el-main-stub"><slot /></div>' },
  'el-menu': { template: '<div class="el-menu-stub"><slot /></div>' },
  'el-menu-item': {
    props: ['index'],
    template: '<div class="el-menu-item-stub" :data-index="index"><slot /></div>',
  },
  'el-badge': {
    props: ['value', 'hidden'],
    template:
      '<span class="el-badge-stub" :data-value="value" :data-hidden="String(hidden)"><slot /></span>',
  },
  'el-icon': { template: '<span class="el-icon-stub"><slot /></span>' },
  'el-dropdown': { template: '<div class="el-dropdown-stub"><slot /></div>' },
  'el-dropdown-menu': { template: '<div class="el-dropdown-menu-stub"><slot /></div>' },
  'el-dropdown-item': { template: '<div class="el-dropdown-item-stub"><slot /></div>' },
  'router-view': { template: '<div class="router-view-stub" />' },
  ChatDotRound: { template: '<i />' },
  Goods: { template: '<i />' },
  Document: { template: '<i />' },
  Tickets: { template: '<i />' },
  Setting: { template: '<i />' },
  ArrowDown: { template: '<i />' },
}

describe('MainLayout 工单徽标', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ticketUnread.value = 0
    routerState.route.path = '/chat'
  })

  it('挂载时以登录态启动轮询', async () => {
    mountLayout()
    await flushPromises()
    expect(startTicketPolling).toHaveBeenCalled()
  })

  it('unread>0 时工单徽标显示数值', async () => {
    ticketUnread.value = 3
    const wrapper = mountLayout()
    await flushPromises()
    const badge = wrapper.find('.el-badge-stub')
    expect(badge.exists()).toBe(true)
    expect(badge.attributes('data-value')).toBe('3')
    expect(badge.attributes('data-hidden')).toBe('false')
  })

  it('unread=0 时徽标隐藏', async () => {
    const wrapper = mountLayout()
    await flushPromises()
    const badge = wrapper.find('.el-badge-stub')
    expect(badge.attributes('data-hidden')).toBe('true')
  })
})

// ---------- 响应式侧栏（P0-1 三档状态机） ----------

// jsdom 无真实视口：改写 window.innerWidth 模拟断点（useBreakpoint 挂载时读取）
function setWindowWidth(w) {
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    writable: true,
    value: w,
  })
}

describe('MainLayout 响应式侧栏', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ticketUnread.value = 0
    routerState.route.path = '/chat'
    setWindowWidth(1024)
  })

  it('<768 手机档：汉堡按钮出现，点击打开抽屉与遮罩', async () => {
    setWindowWidth(375)
    const wrapper = mountLayout()
    await flushPromises()

    expect(wrapper.find('.nav-toggle').exists()).toBe(true)
    expect(wrapper.find('.sidebar.is-open').exists()).toBe(false)

    await wrapper.find('.nav-toggle').trigger('click')
    expect(wrapper.find('.sidebar.is-open').exists()).toBe(true)
    expect(wrapper.find('.nav-backdrop').exists()).toBe(true)
  })

  it('手机档：点击遮罩关闭抽屉', async () => {
    setWindowWidth(375)
    const wrapper = mountLayout()
    await flushPromises()

    await wrapper.find('.nav-toggle').trigger('click')
    expect(wrapper.find('.sidebar.is-open').exists()).toBe(true)

    await wrapper.find('.nav-backdrop').trigger('click')
    expect(wrapper.find('.sidebar.is-open').exists()).toBe(false)
    expect(wrapper.find('.nav-backdrop').exists()).toBe(false)
  })

  it('手机档：路由跳转后抽屉自动关闭', async () => {
    setWindowWidth(375)
    const wrapper = mountLayout()
    await flushPromises()

    await wrapper.find('.nav-toggle').trigger('click')
    expect(wrapper.find('.sidebar.is-open').exists()).toBe(true)

    routerState.route.path = '/tickets'
    await nextTick()
    expect(wrapper.find('.sidebar.is-open').exists()).toBe(false)
    expect(wrapper.find('.nav-backdrop').exists()).toBe(false)
  })

  it('768-1023 平板档：侧栏折叠为图标栏，无汉堡按钮与遮罩', async () => {
    setWindowWidth(800)
    const wrapper = mountLayout()
    await flushPromises()

    expect(wrapper.find('.sidebar.is-collapsed').exists()).toBe(true)
    expect(wrapper.find('.nav-toggle').exists()).toBe(false)
    expect(wrapper.find('.nav-backdrop').exists()).toBe(false)
  })

  it('≥1024 桌面档：完整侧栏不折叠、无汉堡按钮', async () => {
    setWindowWidth(1440)
    const wrapper = mountLayout()
    await flushPromises()

    expect(wrapper.find('.sidebar.is-collapsed').exists()).toBe(false)
    expect(wrapper.find('.sidebar.is-open').exists()).toBe(false)
    expect(wrapper.find('.nav-toggle').exists()).toBe(false)
  })
})
