<template>
  <div class="ops-page">
    <!-- 3.1 故障场景切换 -->
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">故障场景</span>
        </div>
      </template>
      <div class="scenario-row">
        <el-select
          v-model="ops.activeScenario"
          placeholder="选择故障场景"
          clearable
          style="width: 280px"
          @change="(v) => ops.setScenario(v || null).catch(() => {})"
        >
          <el-option label="无故障" :value="null" />
          <el-option
            v-for="s in ops.scenarios"
            :key="s.name"
            :label="s.title"
            :value="s.name"
          />
        </el-select>
        <template v-if="activeScenarioObj">
          <el-tag
            v-for="(sym, i) in activeScenarioObj.symptoms"
            :key="i"
            type="warning"
            class="sym-tag"
          >
            {{ sym }}
          </el-tag>
          <span v-if="activeScenarioObj.root" class="root-hint">
            预期根因：{{ activeScenarioObj.root }}
          </span>
        </template>
        <span v-else class="muted-text">当前无活动故障，指标为正常基线</span>
      </div>
    </el-card>

    <!-- 3.1 服务与指标总览 -->
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">服务与指标</span>
          <el-tag v-if="ops.sourceMode === 'real'" type="success" size="small" effect="plain">
            真实数据
          </el-tag>
          <el-tag v-else type="info" size="small" effect="plain">
            模拟数据
          </el-tag>
          <el-select
            v-model="ops.selectedMetric"
            placeholder="选择指标"
            style="width: 200px"
            @change="onMetricChange"
          >
            <el-option
              v-for="m in ops.metricOptions"
              :key="m"
              :label="m"
              :value="m"
            />
          </el-select>
        </div>
      </template>
      <div class="service-list">
        <el-tag
          v-for="svc in ops.services"
          :key="svc"
          class="service-tag"
          :class="{ 'is-active': svc === ops.selectedService }"
          @click="onServiceClick(svc)"
        >
          {{ svc }}
        </el-tag>
        <span v-if="ops.services.length === 0" class="muted-text">暂无可用服务</span>
      </div>
      <div v-if="metricStats" class="metric-overview">
        <svg
          class="metric-chart"
          :viewBox="`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`"
          preserveAspectRatio="none"
        >
          <polyline
            :points="polylinePoints"
            fill="none"
            stroke="#409eff"
            stroke-width="2"
            stroke-linejoin="round"
            stroke-linecap="round"
          />
        </svg>
        <div class="chart-time">
          <span>{{ fmtTs(firstTs) }}</span>
          <span>{{ fmtTs(lastTs) }}</span>
        </div>
        <div class="metric-stats">
          <div class="stat-item">
            <span class="stat-label">当前</span>
            <span class="stat-value">{{ fmt(metricStats.current) }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">最高</span>
            <span class="stat-value">{{ fmt(metricStats.max) }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">最低</span>
            <span class="stat-value">{{ fmt(metricStats.min) }}</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">平均</span>
            <span class="stat-value">{{ fmt(metricStats.avg) }}</span>
          </div>
        </div>
      </div>
      <el-empty
        v-else
        description="暂无指标数据"
        :image-size="80"
      />
    </el-card>

    <!-- 3.2 诊断输入区 -->
    <el-card shadow="never" class="panel-card">
      <template #header>
        <span class="card-title">故障诊断</span>
      </template>
      <div class="diagnose-row">
        <div class="diagnose-main">
          <el-input
            v-model="query"
            type="textarea"
            :rows="3"
            placeholder="例如：用户下单提示库存查询失败，请稍后重试"
          />
          <div v-if="quickSymptoms.length" class="quick-fill">
            <span class="muted-text">快捷填充：</span>
            <el-button
              v-for="(sym, i) in quickSymptoms"
              :key="i"
              link
              type="primary"
              @click="query = sym"
            >
              {{ sym }}
            </el-button>
          </div>
        </div>
        <div class="diagnose-actions">
          <div class="incident-switch">
            <el-switch v-model="createIncident" />
            <span class="muted-text">诊断后自动创建故障工单</span>
          </div>
          <div class="action-buttons">
            <el-button v-if="ops.diagnosing" type="danger" @click="ops.cancelDiagnose()">
              中断
            </el-button>
            <el-button
              v-else
              type="primary"
              :disabled="!query.trim()"
              @click="startDiagnose"
            >
              开始诊断
            </el-button>
          </div>
        </div>
      </div>
    </el-card>

    <!-- 3.3 SSE 阶段可视化 -->
    <div v-if="showStagePanel">
      <el-card shadow="never" class="panel-card">
        <template #header>
          <span class="card-title">诊断进度</span>
        </template>
        <el-steps
          :active="ops.stageIndex"
          align-center
          finish-status="success"
          :process-status="stepsProcessStatus"
        >
          <el-step title="规划" description="识别故障范围与数据源" />
          <el-step title="采集" description="拉取告警/指标/日志证据" />
          <el-step title="分析" description="根因推理与置信度评估" />
          <el-step title="完成" description="生成诊断报告" />
        </el-steps>
      </el-card>

      <el-card v-if="showPlan" shadow="never" class="panel-card">
        <template #header>
          <span class="card-title">诊断规划</span>
        </template>
        <div class="plan-section">
          <div class="plan-item">
            <span class="plan-label">涉及服务</span>
            <el-tag
              v-for="(svc, i) in toList(ops.plan.services)"
              :key="i"
              class="sym-tag"
            >
              {{ svc }}
            </el-tag>
          </div>
          <div class="plan-item">
            <span class="plan-label">数据源</span>
            <el-tag
              v-for="(ds, i) in toList(ops.plan.data_sources)"
              :key="i"
              type="info"
              class="sym-tag"
            >
              {{ ds }}
            </el-tag>
          </div>
          <div class="plan-item">
            <span class="plan-label">关键词</span>
            <el-tag
              v-for="(kw, i) in toList(ops.plan.keywords)"
              :key="i"
              type="warning"
              class="sym-tag"
            >
              {{ kw }}
            </el-tag>
          </div>
        </div>
      </el-card>

      <el-card v-if="showEvidence" shadow="never" class="panel-card">
        <template #header>
          <span class="card-title">证据采集</span>
        </template>
        <div class="evidence-section">
          <div v-if="evidenceList('alerts').length" class="evidence-group">
            <div class="evidence-title">
              <el-icon><Bell /></el-icon> 告警（{{ evidenceCount('alerts') }}）
            </div>
            <div
              v-for="(item, i) in evidenceList('alerts')"
              :key="i"
              class="evidence-item"
            >
              <el-tag :type="severityType(item.severity)" size="small">
                {{ item.severity || 'alert' }}
              </el-tag>
              <span class="evidence-text">{{ item.message || item.content || '' }}</span>
            </div>
          </div>

          <div v-if="evidenceList('metrics').length" class="evidence-group">
            <div class="evidence-title">
              <el-icon><TrendCharts /></el-icon> 指标（{{ evidenceCount('metrics') }}）
            </div>
            <div
              v-for="(item, i) in evidenceList('metrics')"
              :key="i"
              class="evidence-item"
            >
              <el-tag type="primary" size="small">
                {{ item.service || '' }} / {{ item.metric || '' }}
              </el-tag>
              <span class="evidence-text">
                current={{ fmt(item.current) }}
                <template v-if="item.max !== undefined && item.max !== null">
                  ，max={{ fmt(item.max) }}
                </template>
              </span>
            </div>
          </div>

          <div v-if="evidenceList('logs').length" class="evidence-group">
            <div class="evidence-title">
              <el-icon><Document /></el-icon> 日志（{{ evidenceCount('logs') }}）
            </div>
            <div
              v-for="(item, i) in evidenceList('logs')"
              :key="i"
              class="evidence-item"
            >
              <el-tag :type="levelType(item.level)" size="small">
                {{ item.level || 'log' }}
              </el-tag>
              <span class="evidence-text">{{ truncate(item.message || item.content, 120) }}</span>
            </div>
          </div>

          <div v-if="evidenceList('changes').length" class="evidence-group">
            <div class="evidence-title">
              <el-icon><Edit /></el-icon> 变更（{{ evidenceCount('changes') }}）
            </div>
            <div
              v-for="(item, i) in evidenceList('changes')"
              :key="i"
              class="evidence-item"
            >
              <el-tag type="warning" size="small">{{ item.type || 'change' }}</el-tag>
              <span class="evidence-text">{{ item.content || item.message || '' }}</span>
            </div>
          </div>

          <div v-if="evidenceList('kb').length" class="evidence-group">
            <div class="evidence-title">
              <el-icon><Collection /></el-icon> 知识库命中（{{ evidenceCount('kb') }}）
            </div>
            <div
              v-for="(item, i) in evidenceList('kb')"
              :key="i"
              class="evidence-item"
            >
              <span class="evidence-text">
                {{ item.title || item.content || '' }}
                <el-tag v-if="item.score !== undefined && item.score !== null" type="success" size="small">
                  {{ fmt(item.score) }}
                </el-tag>
              </span>
            </div>
          </div>

          <div v-if="evidenceList('tickets').length" class="evidence-group">
            <div class="evidence-title">
              <el-icon><Tickets /></el-icon> 相似历史工单（{{ evidenceCount('tickets') }}）
            </div>
            <div
              v-for="(item, i) in evidenceList('tickets')"
              :key="i"
              class="evidence-item"
            >
              <el-tag type="info" size="small">{{ item.status || 'ticket' }}</el-tag>
              <span class="evidence-text">{{ truncate(item.title || '', 100) }}</span>
            </div>
          </div>

          <el-empty
            v-if="!showEvidence"
            description="证据采集中…"
            :image-size="60"
          />
        </div>
      </el-card>
    </div>

    <!-- 3.4 根因报告卡片 -->
    <el-card v-if="ops.diagnoseStage === 'done'" shadow="never" class="panel-card report-card">
      <template #header>
        <span class="card-title">诊断报告</span>
      </template>
      <template v-if="reportObj.summary">
        <el-alert
          v-if="reportObj.ticket_id"
          type="success"
          :closable="false"
          show-icon
          class="ticket-alert"
        >
          <template #title>
            已自动创建故障工单 {{ reportObj.ticket_id }}，前往
            <router-link to="/tickets" class="ticket-link">工单列表</router-link>
          </template>
        </el-alert>

        <h2 class="report-title">{{ reportObj.summary }}</h2>

        <div v-if="toList(reportObj.symptoms).length" class="report-section">
          <div class="report-label">症状</div>
          <el-tag
            v-for="(sym, i) in toList(reportObj.symptoms)"
            :key="i"
            type="warning"
            class="sym-tag"
          >
            {{ sym }}
          </el-tag>
        </div>

        <div class="report-section">
          <div class="report-label">根因</div>
          <p class="report-text">{{ reportObj.root_cause || '未给出' }}</p>
        </div>

        <div class="report-section">
          <div class="report-label">处置建议</div>
          <p class="report-text">{{ reportObj.recovery || '未给出' }}</p>
        </div>

        <div v-if="toList(reportObj.affected_services).length" class="report-section">
          <div class="report-label">影响服务</div>
          <el-tag
            v-for="(svc, i) in toList(reportObj.affected_services)"
            :key="i"
            type="danger"
            class="sym-tag"
          >
            {{ svc }}
          </el-tag>
        </div>

        <div v-if="toList(reportObj.affected_hosts).length" class="report-section">
          <div class="report-label">受影响主机</div>
          <el-tag
            v-for="(host, i) in toList(reportObj.affected_hosts)"
            :key="i"
            type="warning"
            effect="plain"
            class="sym-tag"
          >
            {{ host }}
          </el-tag>
        </div>

        <div v-if="hasEvidenceSummary" class="report-section">
          <div class="report-label">证据统计</div>
          <el-descriptions :column="5" border size="small" class="evidence-summary">
            <el-descriptions-item label="告警">
              {{ summaryCount('alerts') }}
            </el-descriptions-item>
            <el-descriptions-item label="指标">
              {{ summaryCount('metrics') }}
            </el-descriptions-item>
            <el-descriptions-item label="日志">
              {{ summaryCount('logs') }}
            </el-descriptions-item>
            <el-descriptions-item label="变更">
              {{ summaryCount('changes') }}
            </el-descriptions-item>
            <el-descriptions-item label="知识库">
              {{ summaryCount('kb') }}
            </el-descriptions-item>
          </el-descriptions>
        </div>

        <div class="report-section confidence-row">
          <div class="report-label">置信度</div>
          <el-progress
            :percentage="confidencePct"
            :stroke-width="14"
            :color="confidenceColor"
            class="confidence-progress"
          />
        </div>
      </template>
      <el-empty v-else description="未生成报告内容" />
    </el-card>

    <el-alert
      v-if="ops.degraded && ops.degraded.length"
      type="warning"
      :closable="false"
      show-icon
      class="degraded-alert"
      :title="`诊断以降级模式完成：${ops.degraded.join('；')}`"
    />

    <el-alert
      v-if="ops.diagnoseStage === 'error'"
      type="error"
      :closable="false"
      show-icon
      class="degraded-alert"
      :title="ops.errorMsg || '诊断失败，请重试'"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useOpsStore } from '@/stores/ops'

const ops = useOpsStore()

const query = ref('')
const createIncident = ref(true)

const SVG_WIDTH = 640
const SVG_HEIGHT = 160
const PAD = 10

// ---------- 3.1 场景与服务/指标 ----------

const activeScenarioObj = computed(
  () => ops.scenarios.find((s) => s.name === ops.activeScenario) || null,
)

const metricStats = computed(() => {
  const pts = ops.metricPoints || []
  if (pts.length === 0) return null
  const values = pts.map((p) => Number(p.value))
  return {
    current: values[values.length - 1],
    max: Math.max(...values),
    min: Math.min(...values),
    avg: values.reduce((a, b) => a + b, 0) / values.length,
  }
})

const polylinePoints = computed(() => {
  const pts = ops.metricPoints || []
  if (pts.length < 2) return ''
  const values = pts.map((p) => Number(p.value))
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const innerW = SVG_WIDTH - PAD * 2
  const innerH = SVG_HEIGHT - PAD * 2
  return pts
    .map((p, i) => {
      const x = PAD + (i / (pts.length - 1)) * innerW
      const y = SVG_HEIGHT - PAD - ((Number(p.value) - min) / range) * innerH
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})

const firstTs = computed(() => {
  const pts = ops.metricPoints || []
  return pts.length ? pts[0].ts : null
})
const lastTs = computed(() => {
  const pts = ops.metricPoints || []
  return pts.length ? pts[pts.length - 1].ts : null
})

function onServiceClick(svc) {
  if (svc === ops.selectedService) return
  ops.loadMetric(svc, ops.selectedMetric).catch(() => {})
}

function onMetricChange(v) {
  ops.loadMetric(ops.selectedService, v).catch(() => {})
}

// ---------- 3.2 诊断输入 ----------

const quickSymptoms = computed(() => {
  const s = ops.scenarios && ops.scenarios[0]
  return (s && Array.isArray(s.symptoms) ? s.symptoms : []).slice(0, 2)
})

async function startDiagnose() {
  const q = query.value.trim()
  if (!q || ops.diagnosing) return
  try {
    await ops.runDiagnose(q, { createIncident: createIncident.value })
  } catch (e) {
    ElMessage.error((e && e.message) || '诊断失败')
  }
}

// ---------- 3.3 SSE 阶段可视化 ----------

const showStagePanel = computed(
  () => ops.diagnosing || !['idle', 'done'].includes(ops.diagnoseStage),
)

const stepsProcessStatus = computed(() =>
  ops.diagnoseStage === 'error' ? 'error' : 'process',
)

const showPlan = computed(
  () =>
    ops.plan &&
    (toList(ops.plan.services).length ||
      toList(ops.plan.data_sources).length ||
      toList(ops.plan.keywords).length),
)

const showEvidence = computed(() => {
  if (!ops.evidence) return false
  return ['alerts', 'metrics', 'logs', 'changes', 'kb', 'tickets'].some(
    (k) => evidenceList(k).length > 0,
  )
})

function evidenceList(key) {
  const v = ops.evidence ? ops.evidence[key] : null
  return Array.isArray(v) ? v : []
}

function evidenceCount(key) {
  return evidenceList(key).length
}

// ---------- 3.4 报告 ----------

const reportObj = computed(() => ops.report || {})

const confidencePct = computed(() =>
  Math.round(Number((ops.report && ops.report.confidence) || 0) * 100),
)

const confidenceColor = computed(() => {
  if (confidencePct.value >= 80) return '#67c23a'
  if (confidencePct.value >= 50) return '#e6a23c'
  return '#f56c6c'
})

const hasEvidenceSummary = computed(
  () =>
    ['alerts', 'metrics', 'logs', 'changes', 'kb'].some(
      (k) => summaryCount(k) > 0,
    ),
)

function summaryCount(key) {
  const es = ops.report && ops.report.evidence_summary
  if (es && es[key] !== undefined && es[key] !== null) return es[key]
  return evidenceCount(key)
}

// ---------- 通用工具 ----------

function toList(v) {
  if (Array.isArray(v)) return v
  if (v === null || v === undefined || v === '') return []
  return [v]
}

function fmt(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '-'
}

function truncate(s, n = 120) {
  if (s === null || s === undefined) return ''
  const str = String(s)
  return str.length > n ? `${str.slice(0, n)}…` : str
}

function fmtTs(ts) {
  if (ts === null || ts === undefined || ts === '') return ''
  let n = Number(ts)
  if (!Number.isFinite(n)) return String(ts)
  if (n < 1e12) n *= 1000
  const d = new Date(n)
  if (Number.isNaN(d.getTime())) return String(ts)
  const pad = (x) => String(x).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const SEVERITY_TYPES = {
  critical: 'danger',
  high: 'danger',
  major: 'danger',
  warning: 'warning',
  medium: 'warning',
  minor: 'info',
  low: 'info',
  info: 'info',
}
function severityType(s) {
  return SEVERITY_TYPES[String(s || '').toLowerCase()] || 'info'
}

const LEVEL_TYPES = {
  error: 'danger',
  critical: 'danger',
  warn: 'warning',
  warning: 'warning',
  info: 'info',
  debug: 'info',
}
function levelType(l) {
  return LEVEL_TYPES[String(l || '').toLowerCase()] || 'info'
}

onMounted(() => {
  Promise.all([ops.loadScenarios(), ops.loadServices()]).catch(() => {})
})
</script>

<style scoped>
.ops-page {
  padding: 16px;
}
.panel-card {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title {
  font-weight: 600;
}
.muted-text {
  color: #909399;
  font-size: 13px;
}

/* 场景区 */
.scenario-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.sym-tag {
  margin-right: 6px;
}
.root-hint {
  color: #e6a23c;
  font-size: 13px;
}

/* 服务与指标 */
.service-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.service-tag {
  cursor: pointer;
}
.service-tag.is-active {
  background-color: #409eff;
  border-color: #409eff;
  color: #fff;
}
.metric-overview {
  display: flex;
  flex-direction: column;
}
.metric-chart {
  width: 100%;
  height: 160px;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
}
.chart-time {
  display: flex;
  justify-content: space-between;
  color: #909399;
  font-size: 12px;
  margin-top: 4px;
}
.metric-stats {
  display: flex;
  gap: 24px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 6px 16px;
  background: #f5f7fa;
  border-radius: 4px;
  min-width: 72px;
}
.stat-label {
  color: #909399;
  font-size: 12px;
}
.stat-value {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

/* 诊断输入 */
.diagnose-row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.diagnose-main {
  flex: 1;
}
.quick-fill {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.diagnose-actions {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 200px;
  flex-shrink: 0;
}
.incident-switch {
  display: flex;
  align-items: center;
  gap: 8px;
}
.action-buttons {
  display: flex;
  gap: 8px;
}

/* 规划与证据 */
.plan-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.plan-item {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.plan-label {
  color: #606266;
  font-size: 13px;
  font-weight: 600;
  width: 64px;
  flex-shrink: 0;
}
.evidence-section {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.evidence-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.evidence-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #303133;
  font-weight: 600;
  font-size: 14px;
}
.evidence-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  color: #606266;
}
.evidence-text {
  line-height: 1.6;
  word-break: break-all;
}

/* 报告 */
.report-title {
  margin: 0 0 16px;
  color: #303133;
}
.ticket-alert {
  margin-bottom: 16px;
}
.ticket-link {
  color: #409eff;
  text-decoration: none;
}
.report-section {
  margin-bottom: 16px;
}
.report-label {
  font-weight: 600;
  color: #606266;
  margin-bottom: 6px;
  font-size: 13px;
}
.report-text {
  margin: 0;
  line-height: 1.7;
  color: #303133;
  white-space: pre-wrap;
}
.confidence-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.confidence-row .report-label {
  margin-bottom: 0;
}
.confidence-progress {
  flex: 1;
}
.degraded-alert {
  margin-top: 16px;
}

@media (max-width: 900px) {
  .diagnose-row {
    flex-direction: column;
  }
  .diagnose-actions {
    width: 100%;
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
  }
}
</style>
