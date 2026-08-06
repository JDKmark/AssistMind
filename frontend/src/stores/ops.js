import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  listScenarios as listScenariosApi,
  setScenario as setScenarioApi,
  listServices as listServicesApi,
  queryMetric as queryMetricApi,
  diagnoseStream,
} from '@/api/ops'

export const useOpsStore = defineStore('ops', () => {
  const scenarios = ref([])
  const activeScenario = ref(null)
  const services = ref([])
  const metricOptions = ref([])
  const selectedService = ref('')
  const selectedMetric = ref('')
  const metricPoints = ref([])
  // 数据源模式（mock=预置场景模拟 / real=Prometheus 真实数据），来自 /ops/services
  const sourceMode = ref('mock')

  const diagnoseStage = ref('idle')
  const plan = ref({})
  const evidence = ref({})
  const report = ref({})
  const degraded = ref([])
  const diagnosing = ref(false)
  const errorMsg = ref('')

  // 内部持有的 AbortController，用于中断正在进行的诊断流（非响应式）
  let abortController = null

  const stageIndex = computed(() => {
    switch (diagnoseStage.value) {
      case 'start':
      case 'planning':
        return 0
      case 'collecting':
      case 'evidence':
        return 1
      case 'analyzing':
        return 2
      case 'done':
        return 3
      default:
        return 0
    }
  })

  async function loadScenarios() {
    const data = await listScenariosApi()
    scenarios.value = data.scenarios || []
  }

  async function setScenario(name) {
    const data = await setScenarioApi(name)
    activeScenario.value = data.active_scenario ?? null
  }

  async function loadServices() {
    const data = await listServicesApi()
    services.value = data.services || []
    metricOptions.value = data.metrics || []
    sourceMode.value = data.source_mode || 'mock'
    if (services.value.length > 0) {
      selectedService.value = services.value[0]
    }
    if (metricOptions.value.length > 0) {
      selectedMetric.value = metricOptions.value[0]
    }
    if (selectedService.value && selectedMetric.value) {
      await loadMetric(selectedService.value, selectedMetric.value)
    }
  }

  async function loadMetric(service, metric) {
    selectedService.value = service
    selectedMetric.value = metric
    const data = await queryMetricApi(service, metric)
    metricPoints.value = data.points || []
  }

  async function runDiagnose(query, { createIncident = true } = {}) {
    // 正在诊断则先中断旧流
    if (diagnosing.value) cancelDiagnose()
    const controller = new AbortController()
    abortController = controller
    resetDiagnose()
    diagnosing.value = true
    try {
      await diagnoseStream(query, {
        createIncident,
        signal: controller.signal,
        onEvent: (eventName, dataObj) => {
          switch (eventName) {
            case 'start':
              diagnoseStage.value = 'start'
              break
            case 'planning':
              diagnoseStage.value = 'planning'
              plan.value = dataObj.plan || {}
              break
            case 'collecting':
              diagnoseStage.value = 'collecting'
              break
            case 'evidence':
              diagnoseStage.value = 'evidence'
              evidence.value = dataObj.evidence || dataObj
              break
            case 'analyzing':
              diagnoseStage.value = 'analyzing'
              break
            case 'done':
              diagnoseStage.value = 'done'
              report.value = dataObj.report || {}
              degraded.value = dataObj.degraded || []
              break
          }
        },
        onDone: () => {
          if (abortController === controller) diagnosing.value = false
        },
        onError: (message) => {
          // 旧流被新流取代或已取消时，忽略其错误回调，避免污染当前状态
          if (abortController !== controller) return
          diagnoseStage.value = 'error'
          errorMsg.value = message
          diagnosing.value = false
          throw new Error(message)
        },
      })
    } finally {
      if (abortController === controller) abortController = null
    }
  }

  function cancelDiagnose() {
    if (abortController) {
      abortController.abort()
      abortController = null
    }
    diagnosing.value = false
  }

  function resetDiagnose() {
    plan.value = {}
    evidence.value = {}
    report.value = {}
    degraded.value = []
    errorMsg.value = ''
    diagnoseStage.value = 'idle'
  }

  return {
    scenarios,
    activeScenario,
    services,
    metricOptions,
    selectedService,
    selectedMetric,
    metricPoints,
    sourceMode,
    diagnoseStage,
    plan,
    evidence,
    report,
    degraded,
    diagnosing,
    errorMsg,
    stageIndex,
    loadScenarios,
    setScenario,
    loadServices,
    loadMetric,
    runDiagnose,
    cancelDiagnose,
    resetDiagnose,
  }
})