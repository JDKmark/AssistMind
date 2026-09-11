<template>
  <div class="knowledge-page">
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">知识库文档<span class="doc-count"> · 共 {{ total }} 篇</span></span>
          <el-button
            v-if="auth.role === 'admin'"
            type="primary"
            :loading="rebuilding"
            @click="handleRebuild"
          >
            {{ rebuilding ? (rebuildStatus || '重建中…') : '重建索引' }}
          </el-button>
        </div>
      </template>

      <el-alert
        v-if="error"
        type="error"
        :closable="false"
        show-icon
        class="list-alert"
        :title="error"
      />

      <!-- 窄屏容器内横滚（P0-3）：页面不横向溢出，表格可滑动查看 -->
      <div class="table-scroll">
      <el-table
        v-loading="loading"
        :data="docs"
        style="width: 100%"
      >
        <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
        <el-table-column prop="source" label="来源" min-width="140" show-overflow-tooltip />
        <el-table-column label="分类" width="110">
          <template #default="{ row }">
            <el-tag
              v-if="row && row.category"
              size="small"
              type="info"
              effect="plain"
              class="am-tag-pill"
            >
              {{ row.category }}
            </el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="chunk_count" label="Chunk 数" width="100">
          <template #default="{ row }">
            <span class="am-mono">{{ row ? row.chunk_count : '' }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="auth.role === 'admin'" label="操作" width="110">
          <template #default="{ row }">
            <el-popconfirm
              :title="`确认删除文档「${row ? row.title : ''}」？删除后不可恢复`"
              @confirm="handleDelete(row)"
            >
              <template #reference>
                <el-button
                  type="danger"
                  link
                  :disabled="deletingDocId !== ''"
                  :loading="deletingDocId === (row ? row.doc_id : '')"
                >
                  删除
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      </div>

      <el-empty
        v-if="!loading && !error && docs.length === 0"
        :image-size="90"
        description="知识库暂无文档"
        class="am-empty-docs"
      >
        <template #image>
          <svg viewBox="0 0 96 96" width="90" height="90" fill="none">
            <rect x="22" y="14" width="52" height="68" rx="8" fill="#eef3fb" stroke="#c2d0f2" stroke-width="2" />
            <path d="M34 32h28M34 44h18M34 56h28M34 68h12" stroke="#5b84e6" stroke-width="2.5" stroke-linecap="round" />
            <circle cx="72" cy="26" r="10" fill="#2352c5" />
            <path d="M72 21v10M67 26h10" stroke="#fff" stroke-width="2" stroke-linecap="round" />
          </svg>
        </template>
      </el-empty>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listDocs, deleteDoc, rebuildIndex, getJobStatus } from '@/api/knowledge'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const loading = ref(false)
const rebuilding = ref(false)
const rebuildStatus = ref('')
const deletingDocId = ref('')
const docs = ref([])
const total = ref(0)
const error = ref('')

// 任务轮询间隔（毫秒）：知识库重建为分钟级 RQ 异步任务
const JOB_POLL_INTERVAL = 2000
// 轮询上限：worker 崩溃时任务会滞留队列，封顶避免按钮无限转圈（150 次 × 2s = 5 分钟）
const JOB_POLL_MAX_ATTEMPTS = 150

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function loadDocs() {
  loading.value = true
  try {
    const data = await listDocs()
    docs.value = data.docs || []
    total.value = data.total || 0
    error.value = data.error || ''
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  } finally {
    loading.value = false
  }
}

async function handleDelete(row) {
  if (!row || !row.doc_id) return
  deletingDocId.value = row.doc_id
  try {
    await deleteDoc(row.doc_id)
    ElMessage.success(`已删除文档 ${row.doc_id}`)
    await loadDocs()
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  } finally {
    deletingDocId.value = ''
  }
}

async function handleRebuild(maxAttempts = JOB_POLL_MAX_ATTEMPTS) {
  rebuilding.value = true
  rebuildStatus.value = ''
  try {
    // 重建为 RQ 异步任务：接口秒回 job_id，耗时执行在 worker 进程
    const data = await rebuildIndex()
    const jobId = data.job_id
    if (!jobId) throw new Error('未返回任务 ID')

    // 轮询任务状态直到终态（finished / failed），封顶防 worker 失联时无限轮询；
    // 组件卸载后停止轮询（不再空转、不在其他页面弹提示）
    let attempts = 0
    let job = null
    while (attempts < maxAttempts && !pollDisposed) {
      attempts += 1
      job = await getJobStatus(jobId)
      if (pollDisposed) return // 查询挂起期间组件已卸载：不弹完成/失败提示，不写状态
      if (job.status === 'finished') {
        const chunks = job.result?.chunks ?? 0
        ElMessage.success(`索引重建完成，共 ${chunks} 个 chunk`)
        break
      }
      if (job.status === 'failed') {
        ElMessage.error(`索引重建失败：${job.error || '未知错误'}`)
        break
      }
      rebuildStatus.value = job.status === 'started' ? '重建中…' : '排队中…'
      await sleep(JOB_POLL_INTERVAL)
    }
    if (!pollDisposed && (!job || (job.status !== 'finished' && job.status !== 'failed'))) {
      ElMessage.error('轮询超时：任务可能仍在后台执行，请检查 worker 进程状态')
    }
  } catch (e) {
    // 入队/轮询失败：request 拦截器已统一提示，这里终止轮询
    console.warn('[Knowledge] 重建任务轮询终止', e)
  } finally {
    rebuilding.value = false
    rebuildStatus.value = ''
  }
}

// 组件卸载置位：进行中的任务轮询停止（下一轮循环退出）
let pollDisposed = false
onUnmounted(() => {
  pollDisposed = true
})

onMounted(loadDocs)
</script>

<style scoped>
.knowledge-page {
  padding: 20px;
}
.panel-card {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 8px 12px;
}
/* 窄屏：表格容器内横滚，页面本体不溢出（P0-3） */
.table-scroll {
  overflow-x: auto;
}
.table-scroll .el-table {
  min-width: 640px;
}
.card-title {
  font-weight: 600;
}
.doc-count {
  font-size: 13px;
  font-weight: 400;
  color: var(--am-text-3);
  letter-spacing: 0.01em;
}
.list-alert {
  margin-bottom: 12px;
}
</style>
