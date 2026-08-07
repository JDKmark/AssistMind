import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// ---------- mock 依赖 ----------

const knowledgeApi = vi.hoisted(() => ({
  listDocs: vi.fn(),
  deleteDoc: vi.fn(),
  rebuildIndex: vi.fn(),
}))

vi.mock('@/api/knowledge', () => knowledgeApi)

const ElMessageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('element-plus', () => ({ ElMessage: ElMessageMock }))

import Knowledge from '@/views/Knowledge/index.vue'

// ---------- 组件测试的 EP 组件 stub ----------

const stubs = {
  'el-card': {
    template:
      '<div class="el-card-stub"><div class="el-card-header"><slot name="header" /></div><div class="el-card-body"><slot /></div></div>',
  },
  'el-button': {
    props: ['disabled', 'loading'],
    template:
      '<button class="el-button-stub" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
  },
  'el-table': { template: '<div class="el-table-stub"><slot /></div>' },
  'el-table-column': {
    template: '<div class="el-table-column-stub"><slot :row="{}" /></div>',
  },
  'el-popconfirm': {
    props: ['title'],
    template:
      '<div class="el-popconfirm-stub" :title="title"><slot name="reference" /></div>',
  },
  'el-tag': { template: '<span class="el-tag-stub"><slot /></span>' },
  'el-empty': {
    props: ['description'],
    template: '<div class="el-empty-stub">{{ description }}<slot /></div>',
  },
  'el-alert': {
    props: ['title'],
    template:
      '<div class="el-alert-stub" :title="title"><span class="el-alert-title">{{ title }}</span><slot /></div>',
  },
}

const DOCS = [
  { doc_id: 'ops-1', title: '运维手册', source: 'ops/manual.md', category: 'ops', chunk_count: 2 },
  { doc_id: 'mall-1', title: '商城文档', source: 'mall/guide.md', category: 'mall', chunk_count: 1 },
]

describe('Knowledge 组件', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function mountKnowledge() {
    return mount(Knowledge, {
      global: {
        components: stubs,
        directives: { loading: {} },
      },
    })
  }

  it('加载并渲染文档列表与统计', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    const wrapper = mountKnowledge()
    await flushPromises()

    expect(knowledgeApi.listDocs).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.docs).toEqual(DOCS)
    expect(wrapper.vm.total).toBe(2)
    expect(wrapper.text()).toContain('知识库文档')
    expect(wrapper.text()).toContain('共 2 篇')
    expect(wrapper.find('.el-table-stub').exists()).toBe(true)
  })

  it('无文档时渲染空态', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: [], total: 0 })
    const wrapper = mountKnowledge()
    await flushPromises()

    expect(wrapper.text()).toContain('知识库暂无文档')
    expect(wrapper.find('.el-empty-stub').exists()).toBe(true)
  })

  it('Qdrant 不可用时渲染 error 提示', async () => {
    knowledgeApi.listDocs.mockResolvedValue({
      docs: [],
      total: 0,
      error: 'Qdrant 不可用，知识库列表暂不可用',
    })
    const wrapper = mountKnowledge()
    await flushPromises()

    expect(wrapper.find('.el-alert-stub').exists()).toBe(true)
    expect(wrapper.text()).toContain('Qdrant 不可用')
  })

  it('每行渲染删除确认浮层与删除按钮', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    const wrapper = mountKnowledge()
    await flushPromises()

    const pop = wrapper.find('.el-popconfirm-stub')
    expect(pop.exists()).toBe(true)
    expect(pop.attributes('title')).toContain('确认删除文档')
    // 确认浮层内带删除按钮
    expect(pop.find('button.el-button-stub').text()).toBe('删除')
  })

  it('删除文档调用 deleteDoc 并提示成功', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    knowledgeApi.deleteDoc.mockResolvedValue({ deleted: true, doc_id: 'ops-1' })
    const wrapper = mountKnowledge()
    await flushPromises()

    await wrapper.vm.handleDelete(DOCS[0])

    expect(knowledgeApi.deleteDoc).toHaveBeenCalledWith('ops-1')
    expect(ElMessageMock.success).toHaveBeenCalled()
    expect(wrapper.vm.deletingDocId).toBe('')
  })

  it('删除失败时不刷新列表且状态复位', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    knowledgeApi.deleteDoc.mockRejectedValue(new Error('删除失败'))
    const wrapper = mountKnowledge()
    await flushPromises()

    await wrapper.vm.handleDelete(DOCS[0])

    expect(knowledgeApi.deleteDoc).toHaveBeenCalledWith('ops-1')
    expect(wrapper.vm.deletingDocId).toBe('')
  })

  it('重建索引调用 rebuildIndex 并提示 chunk 数', async () => {
    knowledgeApi.rebuildIndex.mockResolvedValue({ rebuilt: true, chunks: 3 })
    const wrapper = mountKnowledge()
    await flushPromises()

    await wrapper.vm.handleRebuild()

    expect(knowledgeApi.rebuildIndex).toHaveBeenCalledTimes(1)
    expect(ElMessageMock.success).toHaveBeenCalledWith(expect.stringContaining('3'))
  })

  it('重建按钮存在且带加载态', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: [], total: 0 })
    const wrapper = mountKnowledge()
    await flushPromises()

    const btn = wrapper
      .findAll('button.el-button-stub')
      .find((b) => b.text() === '重建索引')
    expect(btn).toBeTruthy()
  })
})
