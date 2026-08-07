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
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getHealth } from '@/api/health'
import { listDocs } from '@/api/knowledge'
import { listTickets } from '@/api/ticket'

const health = ref(null)
const healthLoading = ref(false)
const kbTotal = ref(0)
const kbChunks = ref(0)
const kbError = ref('')
const tickets = ref([])
const ticketTotal = ref(0)
const dataLoading = ref(false)

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

async function loadOverview() {
  dataLoading.value = true
  try {
    const [kb, ticketData] = await Promise.all([listDocs(), listTickets(null, 200)])
    kbTotal.value = kb.total || 0
    kbChunks.value = (kb.docs || []).reduce((sum, d) => sum + (d.chunk_count || 0), 0)
    kbError.value = kb.error || ''
    tickets.value = ticketData.tickets || []
    ticketTotal.value = ticketData.total != null ? ticketData.total : tickets.value.length
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  } finally {
    dataLoading.value = false
  }
}

onMounted(() => {
  loadHealth()
  loadOverview()
})
</script>

<style scoped>
.admin-page {
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
.overview-grid {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.overview-title {
  font-weight: 600;
  color: #606266;
  font-size: 13px;
  margin-bottom: 8px;
}
</style>
