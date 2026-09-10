<template>
  <div class="admin-page">
    <el-row :gutter="16">
      <!-- 系统状态 -->
      <el-col :xs="24" :sm="12">
        <el-card shadow="never" class="panel-card">
          <template #header>
            <div class="card-header">
              <span class="card-title">系统状态</span>
              <el-tag :type="overallTagType" size="small" effect="plain">
                {{ overallLabel }}
              </el-tag>
            </div>
          </template>
          <el-table :data="depRows" size="small" border style="width: 100%">
            <el-table-column prop="label" label="组件" width="140" />
            <el-table-column label="状态">
              <template #default="{ row }">
                <el-tag :type="row && row.tagType" size="small">
                  {{ row && row.statusText }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <!-- 数据概览 -->
      <el-col :xs="24" :sm="12">
        <el-card shadow="never" class="panel-card">
          <template #header>
            <div class="card-header">
              <span class="card-title">数据概览</span>
              <span v-if="dataLoading" class="muted-text">加载中…</span>
            </div>
          </template>

          <div class="overview-grid">
            <div class="overview-block">
              <div class="overview-title">知识库</div>
              <el-descriptions :column="2" border size="small">
                <el-descriptions-item label="文档数">{{ kbTotal }}</el-descriptions-item>
                <el-descriptions-item label="Chunk 数">{{ kbChunks }}</el-descriptions-item>
              </el-descriptions>
              <div v-if="kbError" class="muted-text">{{ kbError }}</div>
            </div>

            <div class="overview-block">
              <div class="overview-title">工单</div>
              <el-descriptions :column="2" border size="small">
                <el-descriptions-item label="总数">{{ ticketTotal }}</el-descriptions-item>
                <el-descriptions-item
                  v-for="(count, st) in statusDist"
                  :key="st"
                  :label="statusLabel(st)"
                >
                  {{ count }}
                </el-descriptions-item>
              </el-descriptions>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">工单管理</span>
          <div class="ticket-filters">
            <el-select
              v-model="ticketStatusFilter"
              size="small"
              style="width: 120px"
              placeholder="全部状态"
              clearable
              @change="searchTickets"
            >
              <el-option label="待处理" value="open" />
              <el-option label="处理中" value="in_progress" />
              <el-option label="已解决" value="resolved" />
              <el-option label="已关闭" value="closed" />
            </el-select>
            <el-select
              v-model="ticketPriorityFilter"
              size="small"
              style="width: 120px"
              placeholder="全部优先级"
              clearable
              @change="searchTickets"
            >
              <el-option label="紧急" value="urgent" />
              <el-option label="高" value="high" />
              <el-option label="中" value="medium" />
              <el-option label="低" value="low" />
            </el-select>
            <el-input
              v-model="ticketCustomerFilter"
              size="small"
              style="width: 180px"
              placeholder="按客户筛选"
              clearable
              @keyup.enter="searchTickets"
              @clear="searchTickets"
            />
            <el-button size="small" type="primary" @click="searchTickets">查询</el-button>
          </div>
        </div>
      </template>
      <el-table :data="tickets" size="small" border empty-text="暂无工单">
        <el-table-column prop="id" label="工单号" />
        <el-table-column prop="user_id" label="客户" />
        <el-table-column prop="status" label="状态" />
        <el-table-column prop="priority" label="优先级" />
      </el-table>
    </el-card>

    <el-card shadow="never" class="panel-card">
      <template #header><span class="card-title">用户管理</span></template>
      <el-table :data="users" size="small" border empty-text="暂无用户">
        <el-table-column prop="username" label="用户名" />
        <el-table-column prop="role" label="角色" />
        <el-table-column label="状态">
          <template #default="{ row }">{{ row.is_active ? '启用' : '停用' }}</template>
        </el-table-column>
        <el-table-column label="操作">
          <template #default="{ row }">
            <el-button v-if="row.role === 'user'" size="small" @click="changeUserRole(row, 'agent')">设为客服</el-button>
            <el-button v-else-if="row.role === 'agent'" size="small" @click="changeUserRole(row, 'user')">设为用户</el-button>
            <el-button
              v-if="row.role !== 'admin'"
              size="small"
              :type="row.is_active ? 'danger' : 'success'"
              @click="toggleUser(row)"
            >
              {{ row.is_active ? '停用' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="panel-card">
      <template #header><span class="card-title">退款处理</span></template>
      <el-table :data="refunds" size="small" border empty-text="暂无退款">
        <el-table-column prop="refund_id" label="退款单" />
        <el-table-column prop="order_sn" label="订单号" />
        <el-table-column prop="owner_username" label="用户" />
        <el-table-column prop="status" label="状态" />
        <el-table-column label="操作">
          <template #default="{ row }">
            <template v-if="row.status === '处理中'">
              <el-button size="small" type="success" @click="changeRefundStatus(row, '已通过')">通过</el-button>
              <el-button size="small" type="danger" @click="changeRefundStatus(row, '已拒绝')">拒绝</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="panel-card">
      <template #header><span class="card-title">操作审计</span></template>
      <el-table :data="auditLogs" size="small" border empty-text="暂无审计记录">
        <el-table-column prop="actor_username" label="操作者" />
        <el-table-column prop="action" label="动作" />
        <el-table-column prop="target_type" label="目标类型" />
        <el-table-column prop="target_id" label="目标" />
        <el-table-column prop="created_at" label="时间" />
      </el-table>
    </el-card>

    <!-- 商城订单：按归属用户/状态筛选，分页查看 -->
    <el-card shadow="never" class="panel-card mall-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">商城订单</span>
          <div class="mall-filters">
            <el-input
              v-model="orderOwnerFilter"
              size="small"
              style="width: 200px"
              placeholder="按用户筛选，如 user1"
              clearable
              @keyup.enter="searchOrders"
              @clear="searchOrders"
            />
            <el-select
              v-model="orderStatusFilter"
              size="small"
              style="width: 120px"
              placeholder="全部状态"
              clearable
              @change="searchOrders"
            >
              <el-option label="待付款" value="待付款" />
              <el-option label="待发货" value="待发货" />
              <el-option label="已发货" value="已发货" />
              <el-option label="已完成" value="已完成" />
            </el-select>
            <el-button size="small" type="primary" :loading="orderLoading" @click="searchOrders">
              查询
            </el-button>
          </div>
        </div>
      </template>

      <el-table :data="orders" size="small" border empty-text="暂无订单数据">
        <el-table-column label="订单号" min-width="150">
          <template #default="{ row }">{{ row.order_sn || '-' }}</template>
        </el-table-column>
        <el-table-column label="用户" width="100">
          <template #default="{ row }">{{ row.owner_username || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="orderStatusTagType(row.status)" size="small">
              {{ row.status || '-' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="实付金额(¥)" width="110">
          <template #default="{ row }">{{ fmtAmount(row.pay_amount) }}</template>
        </el-table-column>
        <el-table-column label="物流单号" min-width="140">
          <template #default="{ row }">{{ row.logistics_no || '-' }}</template>
        </el-table-column>
        <el-table-column label="下单时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>

      <div class="mall-pagination">
        <el-pagination
          v-model:current-page="orderPage"
          :total="orderTotal"
          :page-size="ORDER_PAGE_SIZE"
          layout="total, prev, pager, next"
          @current-change="loadOrders"
        />
      </div>
    </el-card>

    <!-- 反馈与追溯（Bad Case 归因）：差评列表 → 会话时间线抽屉，借鉴 agent harness 的步骤可视化 -->
    <el-card shadow="never" class="panel-card feedback-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">反馈与追溯</span>
          <div class="feedback-filters">
            <el-select v-model="fbScoreFilter" size="small" style="width: 112px" @change="loadFeedback">
              <el-option label="全部评分" :value="0" />
              <el-option label="差评 1-2" :value="2" />
              <el-option label="好评 4-5" :value="4" />
            </el-select>
            <el-select v-model="fbExportedFilter" size="small" style="width: 128px" @change="loadFeedback">
              <el-option label="全部状态" value="all" />
              <el-option label="已回流评估集" value="exported" />
              <el-option label="待回流" value="pending" />
            </el-select>
            <el-button size="small" :loading="fbLoading" @click="loadFeedback">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table :data="feedbacks" size="small" border empty-text="暂无反馈数据">
        <el-table-column label="评分" width="70">
          <template #default="{ row }">
            <el-tag :type="row.score <= 2 ? 'danger' : 'success'" size="small">{{ row.score }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="问题" min-width="170">
          <template #default="{ row }">{{ row.query || '-' }}</template>
        </el-table-column>
        <el-table-column label="回答摘要" min-width="170">
          <template #default="{ row }">{{ (row.answer || '').slice(0, 40) }}</template>
        </el-table-column>
        <el-table-column label="来源" width="64">
          <template #default="{ row }">{{ (row.sources || []).length }}</template>
        </el-table-column>
        <el-table-column label="时间" width="104">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="openTrace(row)">追溯</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="fbError" class="muted-text">{{ fbError }}</div>
    </el-card>

    <!-- 会话追溯时间线抽屉（步骤可视化：意图 → 检索 → 决策 → 回答 → 证据链） -->
    <el-drawer v-model="traceDrawer" title="会话追溯" size="560px">
      <div v-if="currentTrace" class="trace-body">
        <div class="trace-question">
          <div class="overview-title">问题</div>
          <div>{{ currentTrace.query || '-' }}</div>
          <div class="muted-text">评分 {{ currentTrace.score }} · {{ fmtTime(currentTrace.created_at) }}</div>
        </div>

        <div class="timeline">
          <div class="trace-step">
            <div class="step-label">① 意图路由</div>
            <el-tag size="small" effect="plain">{{ intentLabel(currentTrace.intent) }}</el-tag>
          </div>

          <div class="trace-step">
            <div class="step-label">② 检索来源（{{ (currentTrace.sources || []).length }} 条）</div>
            <div v-if="currentTrace.sources && currentTrace.sources.length">
              <div v-for="(s, i) in currentTrace.sources" :key="i" class="source-block">
                <div class="source-line">
                  [{{ i + 1 }}] {{ s.title || s.doc_id || '知识库命中' }}
                  <span v-if="s.score !== undefined && s.score !== null" class="source-score">
                    {{ fmtScore(s.score) }}
                  </span>
                </div>
                <div class="source-path">{{ s.source }}</div>
                <div class="source-text">{{ s.text || s.snippet || '' }}</div>
              </div>
            </div>
            <div v-else class="muted-text">无检索来源（未命中）</div>
          </div>

          <div class="trace-step">
            <div class="step-label">③ CRAG 决策</div>
            <el-tag size="small" :type="cragTagType">{{ cragLabel }}</el-tag>
            <el-tag
              v-for="(d, i) in currentTrace.degraded || []"
              :key="i"
              size="small"
              type="warning"
              effect="plain"
              class="service-tag"
            >
              降级: {{ d }}
            </el-tag>
          </div>

          <div class="trace-step">
            <div class="step-label">④ 回答</div>
            <div class="trace-answer">{{ currentTrace.answer || '（无回答）' }}</div>
          </div>

          <div v-if="currentTrace.trace_id" class="trace-step">
            <div class="step-label">⑤ 证据链</div>
            <a
              v-if="langfuseHost && currentTrace.trace_id"
              :href="langfuseUrl(currentTrace.trace_id)"
              target="_blank"
              rel="noopener"
              class="trace-link"
            >
              在 Langfuse 查看完整 trace（{{ currentTrace.trace_id }}）
            </a>
            <span v-else class="muted-text">trace_id: {{ currentTrace.trace_id }}</span>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getHealth } from '@/api/health'
import { listFeedback } from '@/api/feedback'
import { listDocs } from '@/api/knowledge'
import { listTickets } from '@/api/ticket'
import { listOrders, listRefunds, updateRefundStatus } from '@/api/mall'
import { getOverview, listUsers, updateUser, listAuditLogs } from '@/api/admin'

const health = ref(null)
const healthLoading = ref(false)
const kbTotal = ref(0)
const kbChunks = ref(0)
const kbError = ref('')
const tickets = ref([])
const ticketTotal = ref(0)
const ticketStatusFilter = ref('')
const ticketPriorityFilter = ref('')
const ticketCustomerFilter = ref('')
const dataLoading = ref(false)
const adminOverview = ref({
  orders: { total: 0, amount: 0, by_status: {} },
  tickets: { total: 0, by_status: {} },
  refunds: { total: 0, by_status: {} },
  users: { total: 0, by_role: {} },
  feedback: { total: 0, negative: 0, pending_export: 0 },
  knowledge: { documents: 0, chunks: 0 },
  degraded: [],
})
const users = ref([])
const userTotal = ref(0)
const refunds = ref([])
const refundTotal = ref(0)
const auditLogs = ref([])
const auditTotal = ref(0)

// 反馈与追溯
const feedbacks = ref([])
const fbLoading = ref(false)
const fbError = ref('')
const fbScoreFilter = ref(0) // 0=全部
const fbExportedFilter = ref('all') // all/exported/pending
const traceDrawer = ref(false)
const currentTrace = ref(null)
const langfuseHost = ref('')

const CRAG_LABELS = {
  generate: '直接生成',
  rewrite_retry: '被动改写重试',
  no_result: '未找到（空检索短路）',
}
const INTENT_LABELS = {
  faq: '知识问答',
  task: '工具操作',
  chat: '自由对话',
  diagnose: '运维诊断',
  unclear: '待澄清',
}

const DEP_LABELS = {
  qdrant: 'Qdrant',
  redis: 'Redis',
  postgres: 'PostgreSQL',
  langfuse: 'Langfuse',
}
const STATUS_TEXTS = { ok: '正常', disabled: '未启用', pending: '待检测', error: '异常' }
const STATUS_TAG_TYPES = { ok: 'success', disabled: 'info', pending: 'warning', error: 'danger' }
const TICKET_LABELS = {
  open: '待处理',
  in_progress: '处理中',
  resolved: '已解决',
  closed: '已关闭',
  other: '其他',
}

const depRows = computed(() => {
  const deps = (health.value && health.value.dependencies) || {}
  return Object.keys(DEP_LABELS).map((key) => {
    const st = deps[key] || 'pending'
    return {
      key,
      label: DEP_LABELS[key],
      status: st,
      statusText: STATUS_TEXTS[st] || st,
      tagType: STATUS_TAG_TYPES[st] || 'info',
    }
  })
})

const depStatuses = computed(() => depRows.value.map((r) => r.status))

const overallLabel = computed(() => {
  if (depStatuses.value.includes('error')) return '异常'
  if (depStatuses.value.includes('pending')) return '部分待定'
  return '正常'
})

const overallTagType = computed(() => {
  if (depStatuses.value.includes('error')) return 'danger'
  if (depStatuses.value.includes('pending')) return 'warning'
  return 'success'
})

const statusDist = computed(() => {
  const dist = { open: 0, in_progress: 0, resolved: 0, closed: 0, other: 0 }
  for (const t of tickets.value) {
    const st = t.status || 'other'
    if (st in dist) {
      dist[st] += 1
    } else {
      dist.other += 1
    }
  }
  return dist
})

function statusLabel(s) {
  return TICKET_LABELS[s] || s
}

async function loadHealth() {
  healthLoading.value = true
  try {
    health.value = await getHealth()
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  } finally {
    healthLoading.value = false
  }
}

async function loadAdminData(includeOverview = true) {
  const requests = [listUsers(), listAuditLogs()]
  if (includeOverview) requests.unshift(getOverview())
  const data = await Promise.all(requests)
  const overview = includeOverview ? data[0] : null
  const userData = includeOverview ? data[1] : data[0]
  const auditData = includeOverview ? data[2] : data[1]
  if (overview) adminOverview.value = overview
  users.value = userData.users || []
  userTotal.value = userData.total || 0
  auditLogs.value = auditData.items || []
  auditTotal.value = auditData.total || 0
}

async function changeUserRole(user, role) {
  await updateUser(user.id, { role })
  await loadAdminData()
}

async function toggleUser(user) {
  await updateUser(user.id, { is_active: !user.is_active })
  await loadAdminData()
}

async function searchTickets() {
  const filters = {}
  if (ticketPriorityFilter.value) filters.priority = ticketPriorityFilter.value
  if (ticketCustomerFilter.value.trim()) filters.user_id = ticketCustomerFilter.value.trim()
  const data = await listTickets(ticketStatusFilter.value || null, 50, filters)
  tickets.value = data.tickets || []
  ticketTotal.value = data.total != null ? data.total : tickets.value.length
}

async function loadRefunds() {
  const data = await listRefunds({ limit: 20, offset: 0 })
  refunds.value = data.refunds || []
  refundTotal.value = data.total || 0
}

async function changeRefundStatus(refund, status) {
  await updateRefundStatus(refund.refund_id, status)
  await Promise.all([loadRefunds(), loadAdminData()])
}

async function loadOverview() {
  dataLoading.value = true
  try {
    const [overview, kb, ticketData] = await Promise.all([
      getOverview(),
      listDocs(),
      listTickets(null, 200),
    ])
    adminOverview.value = overview
    kbTotal.value = kb.total || 0
    kbChunks.value = (kb.docs || []).reduce((sum, d) => sum + (d.chunk_count || 0), 0)
    kbError.value = kb.error || ''
    tickets.value = ticketData.tickets || []
    ticketTotal.value = ticketData.total != null ? ticketData.total : tickets.value.length
  } catch (e) {
    kbError.value = '管理概览加载失败'
  } finally {
    dataLoading.value = false
  }
}

// ---------- 商城订单 ----------

const ORDER_PAGE_SIZE = 10
const ORDER_STATUS_TAG_TYPES = {
  待付款: 'warning',
  待发货: 'info',
  已发货: 'primary',
  已完成: 'success',
}
const orders = ref([])
const orderTotal = ref(0)
const orderPage = ref(1)
const orderLoading = ref(false)
const orderOwnerFilter = ref('')
const orderStatusFilter = ref('')

function orderStatusTagType(status) {
  return ORDER_STATUS_TAG_TYPES[status] || 'info'
}

function fmtAmount(amount) {
  const n = Number(amount)
  return Number.isFinite(n) ? n.toFixed(2) : '-'
}

async function loadOrders() {
  orderLoading.value = true
  try {
    const params = {
      limit: ORDER_PAGE_SIZE,
      offset: (orderPage.value - 1) * ORDER_PAGE_SIZE,
    }
    if (orderOwnerFilter.value.trim()) params.owner_username = orderOwnerFilter.value.trim()
    if (orderStatusFilter.value) params.status = orderStatusFilter.value
    const data = await listOrders(params)
    orders.value = data.orders || []
    orderTotal.value = data.total != null ? data.total : orders.value.length
  } catch (e) {
    orders.value = []
    orderTotal.value = 0
    ElMessage.error('商城订单加载失败')
  } finally {
    orderLoading.value = false
  }
}

function searchOrders() {
  orderPage.value = 1
  loadOrders()
}

// ---------- 反馈与追溯 ----------

async function loadFeedback() {
  fbLoading.value = true
  fbError.value = ''
  try {
    const params = {}
    if (fbScoreFilter.value) params.score = fbScoreFilter.value
    if (fbExportedFilter.value !== 'all') {
      params.exported = fbExportedFilter.value === 'exported'
    }
    const data = await listFeedback(params)
    feedbacks.value = data.items || []
    langfuseHost.value = data.langfuse_host || ''
  } catch (e) {
    // 错误已由 request 拦截器统一提示
    fbError.value = '反馈数据加载失败'
  } finally {
    fbLoading.value = false
  }
}

function openTrace(row) {
  currentTrace.value = row
  traceDrawer.value = true
}

function fmtTime(iso) {
  if (!iso) return ''
  return String(iso).slice(0, 16).replace('T', ' ')
}

function fmtScore(score) {
  const n = Number(score)
  return Number.isFinite(n) ? n.toFixed(2) : ''
}

function intentLabel(intent) {
  return INTENT_LABELS[intent] || intent || '-'
}

const cragLabel = computed(() => {
  const a = currentTrace.value && currentTrace.value.crag_action
  return CRAG_LABELS[a] || a || '-'
})

const cragTagType = computed(() => {
  const a = currentTrace.value && currentTrace.value.crag_action
  if (a === 'no_result') return 'warning'
  if (a === 'rewrite_retry') return 'warning'
  return 'success'
})

function langfuseUrl(traceId) {
  return `${String(langfuseHost.value).replace(/\/+$/, '')}/trace/${traceId}`
}

onMounted(() => {
  loadHealth()
  loadOverview()
  loadAdminData()
  loadRefunds()
  loadOrders()
  loadFeedback()
})
</script>

<style scoped>
.admin-page {
  padding: 20px;
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
  color: var(--am-text-3);
  font-size: 13px;
}
.overview-grid {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.overview-title {
  font-weight: 600;
  color: var(--am-text-2);
  font-size: 13px;
  margin-bottom: 8px;
}

/* 商城订单 */
.mall-card {
  margin-top: 16px;
}
.mall-filters,
.ticket-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.mall-pagination {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}

/* 反馈与追溯 */
.feedback-card {
  margin-top: 16px;
}
.feedback-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.trace-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.trace-question {
  padding: 10px 12px;
  background: var(--am-paper);
  border-radius: 6px;
  font-size: 14px;
  line-height: 1.6;
}
.timeline {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.trace-step {
  padding-left: 12px;
  border-left: 2px solid var(--am-blue-200);
}
.step-label {
  font-weight: 600;
  color: var(--am-text);
  font-size: 13px;
  margin-bottom: 6px;
}
.source-block {
  margin-bottom: 8px;
  padding: 8px;
  background: var(--am-paper);
  border-radius: 6px;
}
.source-line {
  font-weight: 600;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.source-score {
  color: var(--am-blue-600);
  font-size: 12px;
}
.source-path {
  color: var(--am-text-3);
  font-size: 12px;
  margin: 2px 0 4px;
  word-break: break-all;
}
.source-text {
  font-size: 13px;
  line-height: 1.6;
  color: var(--am-text-2);
  white-space: pre-wrap;
  word-break: break-all;
}
.trace-answer {
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
}
.trace-link {
  color: var(--am-blue-600);
  text-decoration: none;
  font-size: 13px;
}
.trace-link:hover {
  text-decoration: underline;
}
.service-tag {
  margin-left: 6px;
}
</style>
