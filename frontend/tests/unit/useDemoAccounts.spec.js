import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'

// 演示账号来自后端接口（DEBUG 门控），此处只验证 composable 的取数/降级行为
vi.mock('@/api/auth', () => ({
  getDemoAccounts: vi.fn(),
}))

import { getDemoAccounts } from '@/api/auth'
import { useDemoAccounts } from '@/composables/useDemoAccounts'

const Harness = defineComponent({
  setup() {
    const { accounts } = useDemoAccounts()
    return () =>
      h(
        'div',
        accounts.value.map((a) => h('span', { class: 'chip' }, a.label)),
      )
  },
})

describe('useDemoAccounts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('挂载后拉取演示账号并渲染', async () => {
    getDemoAccounts.mockResolvedValue([
      { label: '管理员', username: 'admin', password: 'p1', role: 'admin' },
      { label: '客服', username: 'agent', password: 'p2', role: 'agent' },
    ])

    const wrapper = mount(Harness)
    await flushPromises()

    expect(getDemoAccounts).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('.chip').map((n) => n.text())).toEqual(['管理员', '客服'])
  })

  it('接口返回空列表（生产环境）时不渲染任何账号', async () => {
    getDemoAccounts.mockResolvedValue([])

    const wrapper = mount(Harness)
    await flushPromises()

    expect(wrapper.findAll('.chip')).toHaveLength(0)
  })

  it('接口失败时降级为空，不影响页面其他部分', async () => {
    getDemoAccounts.mockRejectedValue(new Error('backend down'))

    const wrapper = mount(Harness)
    await flushPromises()

    expect(wrapper.findAll('.chip')).toHaveLength(0)
  })
})
