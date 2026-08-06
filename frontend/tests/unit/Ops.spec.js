import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

// ---------- mock 依赖 ----------

const opsApi = vi.hoisted(() => ({
  listScenarios: vi.fn(),
  setScenario: vi.fn(),
  listServices: vi.fn(),
  queryMetric: vi.fn(),
  diagnoseStream: vi.fn(),
}))

vi.mock('@/api/ops', () => opsApi)

const ElMessageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('element-plus', () => ({ ElMessage: ElMessageMock }))

import { useOpsStore } from '@/stores/ops'
import Ops from '@/views/Ops/index.vue'

// ---------- 测试数据 ----------

const scenario = {
  name: 'conn_pool_exhausted',
  title: '数据库连接池耗尽',
  symptoms: ['下单接口超时', '连接拒绝'],
  root: '连接池最大连接数过小',
}

// ---------- 组件测试的 EP 组件 stub ----------

const stubs = {
  'el-card': {
    template:
      '<div class="el-card-stub"><div class="el-card-header"><slot name="header" /></div><div class="el-card-body"><slot /></div></div>',
  },
  'el-select': { template: '<div class="el-select-stub"><slot /></div>' },
  'el-option': { template: '<div class="el-option-stub" />' },
  'el-tag': { template: '<span class="el-tag-stub"><slot /></span>' },
  'el-empty': { template: '<div class="el-empty-stub"><slot /></div>' },
  'el-input': { template: '<textarea class="el-input-stub" />' },
  'el-button': {
    props: ['disabled'],
    template:
      '<button class="el-button-stub" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
  },
  'el-switch': { template: '<div class="el-switch-stub" />' },
  'el-steps': { template: '<div class="el-steps-stub"><slot /></div>' },
  'el-step': { template: '<div class="el-step-stub" />' },
  'el-icon': { template: '<span class="el-icon-stub"><slot /></span>' },
  'el-alert': {
    props: ['title'],
    template:
      '<div class="el-alert-stub" :title="title"><slot name="title" /><span class="el-alert-title">{{ title }}</span><slot /></div>',
  },
  'el-descriptions': { template: '<div class="el-descriptions-stub"><slot /></div>' },
  'el-descriptions-item': {
    template: '<div class="el-descriptions-item-stub"><slot /></div>',
  },
  'el-progress': { template: '<div class="el-progress-stub" />' },
  'router-link': { template: '<a class="router-link-stub"><slot /></a>' },
  Bell: { template: '<i class="icon-stub" />' },
  TrendCharts: { template: '<i class="icon-stub" />' },
  Document: { template: '<i class="icon-stub" />' },
  Edit: { template: '<i class="icon-stub" />' },
  Collection: { template: '<i class="icon-stub" />' },
  Tickets: { template: '<i class="icon-stub" />' },
}

// ---------- store 测试 ----------

describe('useOpsStore', () => {
  let pinia
  let store

  beforeEach(() => {
    vi.clearAllMocks()
    pinia = createPinia()
    setActivePinia(pinia)
    store = useOpsStore()
    opsApi.listScenarios.mockResolvedValue({ scenarios: [scenario] })
    opsApi.setScenario.mockResolvedValue({ active_scenario: 'conn_pool_exhausted' })
    opsApi.listServices.mockResolvedValue({
      services: ['order-service'],
      metrics: ['cpu', 'mem'],
      source_mode: 'mock',
    })
    opsApi.queryMetric.mockResolvedValue({
      points: [
        { ts: 1, value: 10 },
        { ts: 2, value: 20 },
      ],
    })
    opsApi.diagnoseStream.mockResolvedValue()
  })

  it('setScenario 更新 activeScenario 并以正确参数调用 api', async () => {
    await store.setScenario('conn_pool_exhausted')
    expect(opsApi.setScenario).toHaveBeenCalledWith('conn_pool_exhausted')
    expect(store.activeScenario).toBe('conn_pool_exhausted')
  })

  it('setScenario 失败时保持原值并向上抛错', async () => {
    opsApi.setScenario.mockRejectedValue(new Error('network'))
    await expect(store.setScenario('conn_pool_exhausted')).rejects.toThrow('network')
    expect(store.activeScenario).toBe(null)
  })

  it('runDiagnose 依次处理 planning/evidence/done 事件流并写入报告', async () => {
    opsApi.diagnoseStream.mockImplementation((query, { onEvent, onDone }) => {
      onEvent('planning', {
        plan: { services: ['order-service'], data_sources: ['metric'], keywords: ['超时'] },
      })
      onEvent('evidence', {
        evidence: { alerts: [{ severity: 'critical', message: '连接池耗尽' }] },
      })
      onEvent('done', {
        report: { summary: '连接池耗尽', root_cause: '连接数过小' },
        degraded: ['指标不全'],
      })
      onDone({
        report: { summary: '连接池耗尽', root_cause: '连接数过小' },
        degraded: ['指标不全'],
      })
      return Promise.resolve()
    })

    await store.runDiagnose('用户下单超时')

    expect(store.diagnoseStage).toBe('done')
    expect(store.plan.services).toEqual(['order-service'])
    expect(store.evidence.alerts).toHaveLength(1)
    expect(store.report.root_cause).toBe('连接数过小')
    expect(store.degraded).toEqual(['指标不全'])
    expect(store.diagnosing).toBe(false)
  })

  it('runDiagnose error 事件流会 reject 并写入错误状态', async () => {
    opsApi.diagnoseStream.mockImplementation((query, { onEvent, onError }) => {
      return new Promise((resolve, reject) => {
        onEvent('error', { message: '诊断服务不可用' })
        try {
          onError('诊断服务不可用')
          resolve()
        } catch (e) {
          reject(e)
        }
      })
    })

    await expect(store.runDiagnose('用户下单超时')).rejects.toThrow('诊断服务不可用')
    expect(store.diagnoseStage).toBe('error')
    expect(store.errorMsg).toBe('诊断服务不可用')
    expect(store.diagnosing).toBe(false)
  })

  it('cancelDiagnose 中断进行中的诊断流', async () => {
    let capturedSignal
    let resolveStream
    opsApi.diagnoseStream.mockImplementation((query, { signal }) => {
      capturedSignal = signal
      return new Promise((resolve) => {
        resolveStream = resolve
      })
    })

    const p = store.runDiagnose('用户下单超时')
    expect(store.diagnosing).toBe(true)

    store.cancelDiagnose()
    expect(store.diagnosing).toBe(false)
    expect(capturedSignal.aborted).toBe(true)

    // 清理 pending promise，避免悬挂
    resolveStream()
    await p
  })

  it('loadServices 填充服务/指标并自动加载首个指标', async () => {
    await store.loadServices()

    expect(store.services).toEqual(['order-service'])
    expect(store.metricOptions).toEqual(['cpu', 'mem'])
    expect(store.selectedService).toBe('order-service')
    expect(store.selectedMetric).toBe('cpu')
    expect(store.sourceMode).toBe('mock')
    expect(opsApi.queryMetric).toHaveBeenCalledWith('order-service', 'cpu')
    expect(store.metricPoints).toEqual([
      { ts: 1, value: 10 },
      { ts: 2, value: 20 },
    ])
  })

  it('loadServices 从响应读取真实数据源模式', async () => {
    opsApi.listServices.mockResolvedValue({
      services: ['order-service'],
      metrics: ['cpu', 'mem'],
      source_mode: 'real',
    })
    await store.loadServices()
    expect(store.sourceMode).toBe('real')
  })
})

// ---------- 组件测试 ----------

describe('Ops 组件', () => {
  let pinia

  beforeEach(() => {
    vi.clearAllMocks()
    pinia = createPinia()
    setActivePinia(pinia)
    opsApi.listScenarios.mockResolvedValue({ scenarios: [scenario] })
    opsApi.setScenario.mockResolvedValue({ active_scenario: 'conn_pool_exhausted' })
    opsApi.listServices.mockResolvedValue({ services: ['order-service'], metrics: ['cpu'] })
    opsApi.queryMetric.mockResolvedValue({ points: [{ ts: 1, value: 0.5 }] })
    opsApi.diagnoseStream.mockResolvedValue()
  })

  function mountOps() {
    return mount(Ops, { global: { plugins: [pinia], components: stubs } })
  }

  it('mount 后渲染故障场景选择器', async () => {
    const wrapper = mountOps()
    await flushPromises()

    expect(wrapper.text()).toContain('故障场景')
    expect(wrapper.find('.el-select-stub').exists()).toBe(true)
  })

  it('按数据源模式渲染 模拟/真实数据 标签', async () => {
    opsApi.listServices.mockResolvedValue({
      services: ['order-service'],
      metrics: ['cpu'],
      source_mode: 'real',
    })
    const wrapper = mountOps()
    await flushPromises()
    expect(wrapper.text()).toContain('真实数据')

    const store = useOpsStore()
    store.sourceMode = 'mock'
    await nextTick()
    expect(wrapper.text()).toContain('模拟数据')
  })

  it('开始诊断按钮存在且 query 为空时禁用', async () => {
    const wrapper = mountOps()
    await flushPromises()

    const btn = wrapper
      .findAll('button.el-button-stub')
      .find((b) => b.text() === '开始诊断')
    expect(btn).toBeTruthy()
    expect(btn.attributes('disabled')).toBeDefined()

    wrapper.vm.query = '用户下单提示库存查询失败，请稍后重试'
    await nextTick()
    expect(btn.attributes('disabled')).toBeUndefined()
  })

  it('诊断完成后渲染根因与自动建单提示', async () => {
    const wrapper = mountOps()
    await flushPromises()

    const ops = useOpsStore()
    ops.diagnoseStage = 'done'
    ops.report = {
      summary: '连接池耗尽导致下单接口超时',
      root_cause: '数据库连接池最大连接数配置过小，高峰期连接耗尽',
      recovery: '扩容连接池并优化连接复用',
      ticket_id: 'TK-20260804-1234567',
      symptoms: ['下单超时'],
      affected_services: ['order-service'],
      affected_hosts: ['order-prod-01', 'order-prod-02'],
      confidence: 0.92,
    }
    ops.degraded = []
    await nextTick()

    expect(wrapper.text()).toContain('连接池耗尽导致下单接口超时')
    expect(wrapper.text()).toContain('数据库连接池最大连接数配置过小，高峰期连接耗尽')
    expect(wrapper.text()).toContain('已自动创建故障工单')
    expect(wrapper.text()).toContain('TK-20260804-1234567')
    expect(wrapper.text()).toContain('受影响主机')
    expect(wrapper.text()).toContain('order-prod-01')
  })

  it('证据面板渲染相似历史工单', async () => {
    const wrapper = mountOps()
    await flushPromises()

    const ops = useOpsStore()
    ops.diagnoseStage = 'evidence'
    ops.evidence = {
      alerts: [],
      metrics: [],
      logs: [],
      changes: [],
      kb: [],
      tickets: [
        { id: 'TK-OLD1', title: '连接池耗尽故障', status: 'resolved' },
      ],
      hosts: { 'order-service': ['order-prod-01'] },
    }
    await nextTick()

    expect(wrapper.text()).toContain('相似历史工单')
    expect(wrapper.text()).toContain('连接池耗尽故障')
  })

  it('诊断失败时渲染错误 alert 文本', async () => {
    const wrapper = mountOps()
    await flushPromises()

    const ops = useOpsStore()
    ops.diagnoseStage = 'error'
    ops.errorMsg = '诊断服务暂时不可用'
    await nextTick()

    expect(wrapper.text()).toContain('诊断服务暂时不可用')
    expect(wrapper.find('.el-alert-stub').exists()).toBe(true)
  })
})
