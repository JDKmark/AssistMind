import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// ---------- mock 依赖 ----------

const knowledgeApi = vi.hoisted(() => ({
  listDocs: vi.fn(),
  deleteDoc: vi.fn(),
  rebuildIndex: vi.fn(),
  getJobStatus: vi.fn(),
  toggleDoc: vi.fn(),
  reingestDoc: vi.fn(),
}))

vi.mock('@/api/knowledge', () => knowledgeApi)

const ElMessageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('element-plus', () => ({ ElMessage: ElMessageMock }))

const authMock = vi.hoisted(() => ({ role: 'admin' }))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => authMock,
}))

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
  'el-switch': {
    props: ['modelValue', 'loading'],
    template: '<span class="el-switch-stub" />',
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
    authMock.role = 'admin'
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

  it('每行渲染重灌与删除确认浮层（重灌在删除前）', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    const wrapper = mountKnowledge()
    await flushPromises()

    const pops = wrapper.findAll('.el-popconfirm-stub')
    const reingestPop = pops.find((p) => (p.attributes('title') || '').includes('确认重新灌库'))
    const deletePop = pops.find((p) => (p.attributes('title') || '').includes('确认删除文档'))
    expect(reingestPop).toBeTruthy()
    expect(deletePop).toBeTruthy()
    // 确认浮层内分别带重灌 / 删除按钮
    expect(reingestPop.find('button.el-button-stub').text()).toBe('重新灌库')
    expect(deletePop.find('button.el-button-stub').text()).toBe('删除')
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

  it('停用开关：toggleDoc 成功后本地同步 enabled 并提示', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    knowledgeApi.toggleDoc.mockResolvedValue({ doc_id: 'ops-1', enabled: false })
    const wrapper = mountKnowledge()
    await flushPromises()

    const row = { ...DOCS[0], enabled: true }
    await wrapper.vm.handleToggle(row, false)

    expect(knowledgeApi.toggleDoc).toHaveBeenCalledWith('ops-1', false)
    expect(row.enabled).toBe(false)
    expect(ElMessageMock.success).toHaveBeenCalledWith('已停用检索')
    expect(wrapper.vm.togglingDocId).toBe('')
  })

  it('停用开关：恢复检索提示成功且 enabled 同步为 true', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    knowledgeApi.toggleDoc.mockResolvedValue({ doc_id: 'ops-1', enabled: true })
    const wrapper = mountKnowledge()
    await flushPromises()

    const row = { ...DOCS[0], enabled: false }
    await wrapper.vm.handleToggle(row, true)

    expect(knowledgeApi.toggleDoc).toHaveBeenCalledWith('ops-1', true)
    expect(row.enabled).toBe(true)
    expect(ElMessageMock.success).toHaveBeenCalledWith('已恢复检索')
  })

  it('停用开关失败：回滚列表（重新 loadDocs）且不提示成功', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    knowledgeApi.toggleDoc.mockRejectedValue(new Error('停用失败'))
    const wrapper = mountKnowledge()
    await flushPromises()

    const callsBefore = knowledgeApi.listDocs.mock.calls.length
    await wrapper.vm.handleToggle({ ...DOCS[0] }, false)

    expect(ElMessageMock.success).not.toHaveBeenCalled()
    expect(knowledgeApi.listDocs.mock.calls.length).toBe(callsBefore + 1)
    expect(wrapper.vm.togglingDocId).toBe('')
  })

  it('重新灌库：入队后轮询完成，提示 chunk 数并刷新列表', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    knowledgeApi.reingestDoc.mockResolvedValue({ job_id: 'job-r1', status: 'queued' })
    knowledgeApi.getJobStatus.mockResolvedValue({
      job_id: 'job-r1',
      status: 'finished',
      result: { ingested: true, doc_id: 'ops-1', chunks: 7 },
    })
    const wrapper = mountKnowledge()
    await flushPromises()

    const callsBefore = knowledgeApi.listDocs.mock.calls.length
    await wrapper.vm.handleReingest(DOCS[0])

    expect(knowledgeApi.reingestDoc).toHaveBeenCalledWith('ops-1')
    expect(knowledgeApi.getJobStatus).toHaveBeenCalledWith('job-r1')
    expect(ElMessageMock.success).toHaveBeenCalledWith('重灌完成：7 个 chunk')
    expect(knowledgeApi.listDocs.mock.calls.length).toBe(callsBefore + 1)
    expect(wrapper.vm.reingestingDocId).toBe('')
  })

  it('重新灌库任务失败时提示错误且不刷新列表', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    knowledgeApi.reingestDoc.mockResolvedValue({ job_id: 'job-r2', status: 'queued' })
    knowledgeApi.getJobStatus.mockResolvedValue({
      job_id: 'job-r2',
      status: 'failed',
      error: '源文件不存在',
    })
    const wrapper = mountKnowledge()
    await flushPromises()

    const callsBefore = knowledgeApi.listDocs.mock.calls.length
    await wrapper.vm.handleReingest(DOCS[0])

    expect(ElMessageMock.error).toHaveBeenCalledWith(expect.stringContaining('源文件不存在'))
    expect(knowledgeApi.listDocs.mock.calls.length).toBe(callsBefore)
    expect(wrapper.vm.reingestingDocId).toBe('')
  })

  it('重新灌库入队响应缺 job_id 时终止且不轮询', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    knowledgeApi.reingestDoc.mockResolvedValue({ status: 'queued' })
    const wrapper = mountKnowledge()
    await flushPromises()

    await wrapper.vm.handleReingest(DOCS[0])

    expect(knowledgeApi.getJobStatus).not.toHaveBeenCalled()
    expect(wrapper.vm.reingestingDocId).toBe('')
  })

  it('重建索引入队并轮询到完成，提示 chunk 数', async () => {
    knowledgeApi.rebuildIndex.mockResolvedValue({ job_id: 'job-1', status: 'queued' })
    knowledgeApi.getJobStatus.mockResolvedValue({
      job_id: 'job-1',
      status: 'finished',
      result: { rebuilt: true, chunks: 3 },
    })
    const wrapper = mountKnowledge()
    await flushPromises()

    await wrapper.vm.handleRebuild()

    expect(knowledgeApi.rebuildIndex).toHaveBeenCalledTimes(1)
    expect(knowledgeApi.getJobStatus).toHaveBeenCalledWith('job-1')
    expect(ElMessageMock.success).toHaveBeenCalledWith(expect.stringContaining('3'))
    expect(wrapper.vm.rebuilding).toBe(false)
  })

  it('重建任务失败时提示错误并复位按钮状态', async () => {
    knowledgeApi.rebuildIndex.mockResolvedValue({ job_id: 'job-2', status: 'queued' })
    knowledgeApi.getJobStatus.mockResolvedValue({
      job_id: 'job-2',
      status: 'failed',
      error: 'Qdrant 不可用',
    })
    const wrapper = mountKnowledge()
    await flushPromises()

    await wrapper.vm.handleRebuild()

    expect(ElMessageMock.error).toHaveBeenCalledWith(expect.stringContaining('Qdrant 不可用'))
    expect(wrapper.vm.rebuilding).toBe(false)
  })

  it('入队响应缺 job_id 时终止且不轮询', async () => {
    knowledgeApi.rebuildIndex.mockResolvedValue({ status: 'queued' })
    const wrapper = mountKnowledge()
    await flushPromises()

    await wrapper.vm.handleRebuild()

    expect(knowledgeApi.getJobStatus).not.toHaveBeenCalled()
    expect(wrapper.vm.rebuilding).toBe(false)
  })

  it('轮询超过上限时提示超时并复位按钮状态', async () => {
    knowledgeApi.listDocs.mockResolvedValue({ docs: [], total: 0 })
    knowledgeApi.rebuildIndex.mockResolvedValue({ job_id: 'job-3', status: 'queued' })
    // 任务一直停在 queued（模拟 worker 失联/任务滞留队列）
    knowledgeApi.getJobStatus.mockResolvedValue({ job_id: 'job-3', status: 'queued' })
    const wrapper = mountKnowledge()
    await flushPromises()

    vi.useFakeTimers()
    try {
      // 上限传小值便于测试：3 次轮询后应超时终止而非无限转圈
      const polling = wrapper.vm.handleRebuild(3)
      await vi.advanceTimersByTimeAsync(2000 * 4)
      await polling
    } finally {
      vi.useRealTimers()
    }

    expect(knowledgeApi.getJobStatus).toHaveBeenCalledTimes(3)
    expect(ElMessageMock.error).toHaveBeenCalledWith(expect.stringContaining('轮询超时'))
    expect(wrapper.vm.rebuilding).toBe(false)
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

  it('agent 视角：隐藏重建索引与删除按钮，文档列表正常渲染', async () => {
    authMock.role = 'agent'
    knowledgeApi.listDocs.mockResolvedValue({ docs: DOCS, total: 2 })
    const wrapper = mountKnowledge()
    await flushPromises()

    const buttons = wrapper.findAll('button.el-button-stub')
    expect(buttons.find((b) => b.text() === '重建索引')).toBeUndefined()
    expect(buttons.find((b) => b.text() === '删除')).toBeUndefined()
    expect(wrapper.find('.el-popconfirm-stub').exists()).toBe(false)
    // 文档列表正常展示
    expect(knowledgeApi.listDocs).toHaveBeenCalledTimes(1)
    expect(wrapper.vm.docs).toEqual(DOCS)
    expect(wrapper.vm.total).toBe(2)
    expect(wrapper.find('.el-table-stub').exists()).toBe(true)
  })
})
