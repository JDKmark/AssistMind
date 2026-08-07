import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

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

vi.mock('@/api/health', () => healthApi)
vi.mock('@/api/knowledge', () => knowledgeApi)
vi.mock('@/api/ticket', () => ticketApi)

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
  'el-table': { template: '<div class="el-table-stub"><slot /></div>' },
  'el-table-column': {
    template: '<div class="el-table-column-stub"><slot :row="{}" /></div>',
  },
  'el-descriptions': { template: '<div class="el-descriptions-stub"><slot /></div>' },
  'el-descriptions-item': {
    props: ['label'],
    template:
      '<div class="el-descriptions-item-stub"><span class="el-descriptions-label">{{ label }}</span><slot /></div>',
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
  })

  function mountAdmin() {
    return mount(Admin, { global: { components: stubs } })
  }

  it('渲染系统状态与数据概览，并加载健康/知识库/工单数据', async () => {
    const wrapper = mountAdmin()
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
})
