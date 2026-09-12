<template>
  <div class="knowledge-page">
    <el-alert
      v-if="testDegraded.length"
      type="warning"
      show-icon
      closable
      class="degraded-alert"
      :title="`部分检索源降级：${testDegraded.join('、')}`"
    />

    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">知识库文档<span class="doc-count"> · 共 {{ total }} 篇</span></span>
          <div class="card-actions">
            <el-button
              v-if="auth.role === 'admin'"
              type="primary"
              :loading="rebuilding"
              @click="handleRebuild"
            >
              {{ rebuilding ? (rebuildStatus || '重建中…') : '重建索引' }}
            </el-button>
            <el-button
              v-if="auth.role === 'admin'"
              @click="openUploadDialog"
            >
              上传文档
            </el-button>
          </div>
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
        <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span>{{ row ? row.title : '' }}</span>
            <!-- 停用标识：staff 可见（不加 role 限制），存量数据缺省 enabled 视为启用 -->
            <el-tag
              v-if="row && row.enabled === false"
              size="small"
              type="danger"
              class="disabled-tag"
            >
              已停用
            </el-tag>
          </template>
        </el-table-column>
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
        <el-table-column v-if="auth.role === 'admin'" label="参与检索" width="100">
          <template #default="{ row }">
            <el-switch
              :model-value="row ? row.enabled !== false : true"
              :active-value="true"
              :inactive-value="false"
              :loading="togglingDocId === (row ? row.doc_id : '')"
              @change="(val) => handleToggle(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <el-table-column v-if="auth.role === 'admin'" label="操作" width="170">
          <template #default="{ row }">
            <el-popconfirm
              :title="`确认重新灌库「${row ? row.title : ''}」？将重新解析源文件并覆盖现有 chunks`"
              :width="280"
              @confirm="handleReingest(row)"
            >
              <template #reference>
                <el-button
                  type="primary"
                  link
                  :disabled="reingestingDocId !== ''"
                  :loading="reingestingDocId === (row ? row.doc_id : '')"
                >
                  重新灌库
                </el-button>
              </template>
            </el-popconfirm>
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

    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-header">
          <span class="card-title">召回测试</span>
        </div>
      </template>

      <div class="test-form">
        <el-input
          v-model="testQuery"
          placeholder="输入测试问题，验证知识库召回效果"
          clearable
          class="test-query"
          @keyup.enter="runSearchTest"
        />
        <el-input-number v-model="testTopK" :min="1" :max="20" :step="1" label="top_k" class="test-topk" />
        <el-button type="primary" :loading="testRunning" @click="runSearchTest">执行</el-button>
      </div>

      <template v-if="testResult">
        <el-empty
          v-if="!fusedHits.length && !vectorHits.length && !bm25Hits.length"
          description="无命中结果"
          :image-size="90"
          class="test-empty"
        />
        <template v-else>
          <div class="test-section">
            <div class="test-section-title">
              RRF 融合
              <el-tag size="small" type="primary" effect="plain" class="am-tag-pill">{{ fusedHits.length }} 条</el-tag>
            </div>
            <el-table :data="fusedHits" size="small">
              <el-table-column prop="doc_id" label="doc_id" min-width="150" show-overflow-tooltip />
              <el-table-column prop="section_title" label="段落" min-width="120" show-overflow-tooltip />
              <el-table-column label="得分" width="90">
                <template #default="{ row }">
                  <span class="am-mono">{{ fmtScore(row.rrf_score ?? row.score) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="内容摘要" min-width="220" show-overflow-tooltip>
                <template #default="{ row }">{{ summarizeText(row.text) }}</template>
              </el-table-column>
            </el-table>
          </div>

          <div class="test-section">
            <div class="test-section-title">
              向量召回
              <el-tag size="small" type="info" effect="plain" class="am-tag-pill">{{ vectorHits.length }} 条</el-tag>
            </div>
            <el-table :data="vectorHits" size="small">
              <el-table-column prop="doc_id" label="doc_id" min-width="150" show-overflow-tooltip />
              <el-table-column prop="section_title" label="段落" min-width="120" show-overflow-tooltip />
              <el-table-column label="得分" width="90">
                <template #default="{ row }">
                  <span class="am-mono">{{ fmtScore(row.score) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="内容摘要" min-width="220" show-overflow-tooltip>
                <template #default="{ row }">{{ summarizeText(row.text) }}</template>
              </el-table-column>
            </el-table>
          </div>

          <div class="test-section">
            <div class="test-section-title">
              BM25 召回
              <el-tag size="small" type="info" effect="plain" class="am-tag-pill">{{ bm25Hits.length }} 条</el-tag>
            </div>
            <el-table :data="bm25Hits" size="small">
              <el-table-column prop="doc_id" label="doc_id" min-width="150" show-overflow-tooltip />
              <el-table-column prop="section_title" label="段落" min-width="120" show-overflow-tooltip />
              <el-table-column label="得分" width="90">
                <template #default="{ row }">
                  <span class="am-mono">{{ fmtScore(row.score) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="内容摘要" min-width="220" show-overflow-tooltip>
                <template #default="{ row }">{{ summarizeText(row.text) }}</template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </template>
    </el-card>

    <!-- 上传文档对话框（admin）：选择文件 + 可选分类，入队 RQ 任务后复用任务轮询 -->
    <el-dialog
      v-model="uploadVisible"
      title="上传文档"
      width="480px"
      :close-on-click-modal="!uploading"
      @closed="resetUploadForm"
    >
      <el-upload
        ref="uploadRef"
        drag
        :auto-upload="false"
        :limit="1"
        accept=".md,.txt,.pdf,.docx,.sql,.yml,.yaml"
        :on-change="handleUploadChange"
        :on-remove="handleUploadRemove"
        :on-exceed="handleUploadExceed"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽文件到此处，或<em>点击选择</em></div>
        <template #tip>
          <div class="el-upload__tip">
            支持 .md / .txt / .pdf / .docx / .sql / .yml / .yaml，单个文件不超过 5MB
          </div>
        </template>
      </el-upload>
      <el-input
        v-model="uploadCategory"
        placeholder="分类（可选，默认 upload）"
        clearable
        class="upload-category"
      />
      <template #footer>
        <el-button :disabled="uploading" @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="confirmUpload">
          {{ uploading ? (uploadStatus || '处理中…') : '确认上传' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 文档 chunk 明细抽屉：按 chunk 展示切片文本（停用文档在标题旁展示标识，staff 可见） -->
    <el-drawer v-model="detailVisible" size="45%">
      <template #header>
        <div class="drawer-header">
          <span class="drawer-title">{{ detailTitle }}</span>
          <el-tag v-if="detailDisabled" size="small" type="danger">已停用</el-tag>
        </div>
      </template>
      <div v-loading="detailLoading" class="detail-body">
        <el-collapse v-if="detailChunks && detailChunks.length">
          <el-collapse-item
            v-for="chunk in detailChunks"
            :key="chunk.chunk_index"
            :name="chunk.chunk_index"
          >
            <template #title>
              <span class="chunk-title">#{{ chunkIndexLabel(chunk) }} {{ chunk.section_title || chunk.title }}</span>
            </template>
            <div class="chunk-text am-mono">{{ chunk.text }}</div>
          </el-collapse-item>
        </el-collapse>
        <el-empty
          v-else-if="!detailLoading"
          description="暂无 chunk 数据（文档不存在或 Qdrant 不可用）"
          :image-size="90"
        />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import {
  listDocs,
  deleteDoc,
  rebuildIndex,
  getJobStatus,
  uploadDoc,
  getDocChunks,
  searchTest,
  toggleDoc,
  reingestDoc,
} from '@/api/knowledge'
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

// 任务轮询公共化：重建 / 上传共用（入队接口秒回 job_id，耗时执行在 worker 进程）。
// 轮询任务状态直到终态（finished / failed），封顶防 worker 失联时无限轮询；
// 组件卸载后停止轮询（不再空转、不在其他页面弹提示）。
// 终态与过程文案通过回调交由调用方处理；请求失败向上抛出由调用方 catch。
async function pollJob(jobId, { maxAttempts = JOB_POLL_MAX_ATTEMPTS, onSuccess, onFail, onPending } = {}) {
  let attempts = 0
  let job = null
  while (attempts < maxAttempts && !pollDisposed) {
    attempts += 1
    job = await getJobStatus(jobId)
    if (pollDisposed) return // 查询挂起期间组件已卸载：不弹完成/失败提示，不写状态
    if (job.status === 'finished') {
      if (onSuccess) onSuccess(job)
      return
    }
    if (job.status === 'failed') {
      if (onFail) onFail(job)
      return
    }
    if (onPending) onPending(job)
    await sleep(JOB_POLL_INTERVAL)
  }
  if (!pollDisposed && (!job || (job.status !== 'finished' && job.status !== 'failed'))) {
    ElMessage.error('轮询超时：任务可能仍在后台执行，请检查 worker 进程状态')
  }
}

async function handleRebuild(maxAttempts = JOB_POLL_MAX_ATTEMPTS) {
  rebuilding.value = true
  rebuildStatus.value = ''
  try {
    const data = await rebuildIndex()
    const jobId = data.job_id
    if (!jobId) throw new Error('未返回任务 ID')

    await pollJob(jobId, {
      maxAttempts,
      onSuccess: (job) => {
        const chunks = job.result?.chunks ?? 0
        ElMessage.success(`索引重建完成，共 ${chunks} 个 chunk`)
      },
      onFail: (job) => {
        ElMessage.error(`索引重建失败：${job.error || '未知错误'}`)
      },
      onPending: (job) => {
        rebuildStatus.value = job.status === 'started' ? '重建中…' : '排队中…'
      },
    })
  } catch (e) {
    // 入队/轮询失败：request 拦截器已统一提示，这里终止轮询
    console.warn('[Knowledge] 重建任务轮询终止', e)
  } finally {
    rebuilding.value = false
    rebuildStatus.value = ''
  }
}

// ---------- 上传文档（admin） ----------

const uploadVisible = ref(false)
const uploading = ref(false)
const uploadStatus = ref('')
const uploadFile = ref(null)
const uploadCategory = ref('')
const uploadRef = ref(null)

function openUploadDialog() {
  uploadFile.value = null
  uploadCategory.value = ''
  uploadStatus.value = ''
  uploadVisible.value = true
  // 清理上一次对话框遗留的文件列表（el-upload 内部状态）
  nextTick(() => {
    uploadRef.value?.clearFiles?.()
  })
}

function resetUploadForm() {
  uploadFile.value = null
  uploadCategory.value = ''
  uploadStatus.value = ''
  uploadRef.value?.clearFiles?.()
}

function handleUploadChange(file) {
  uploadFile.value = file?.raw || null
}

function handleUploadRemove() {
  uploadFile.value = null
}

function handleUploadExceed() {
  ElMessage.warning('一次只能上传 1 个文件，请先移除已选文件')
}

async function confirmUpload() {
  if (!uploadFile.value) {
    ElMessage.warning('请先选择要上传的文档')
    return
  }
  uploading.value = true
  uploadStatus.value = ''
  try {
    // 入队为 RQ 异步任务：接口秒回 job_id，入库执行在 worker 进程
    const formData = new FormData()
    formData.append('file', uploadFile.value)
    if (uploadCategory.value) formData.append('category', uploadCategory.value)
    const data = await uploadDoc(formData)
    const jobId = data.job_id
    if (!jobId) throw new Error('未返回任务 ID')

    await pollJob(jobId, {
      onSuccess: async (job) => {
        const docId = job.result?.doc_id || uploadFile.value?.name || ''
        const chunks = job.result?.chunks ?? 0
        ElMessage.success(`上传完成：${docId}，${chunks} 个 chunk`)
        // 轮询期间对话框可能已被用户关闭：成功提示与列表刷新仍要发生
        uploadVisible.value = false
        await loadDocs()
      },
      onFail: (job) => {
        ElMessage.error(`上传失败：${job.error || '未知错误'}`)
      },
      onPending: (job) => {
        uploadStatus.value = job.status === 'started' ? '入库中…' : '排队中…'
      },
    })
  } catch (e) {
    // 上传/入队失败：request 拦截器已统一提示
    console.warn('[Knowledge] 上传任务终止', e)
  } finally {
    uploading.value = false
    uploadStatus.value = ''
  }
}

// ---------- 启停检索 / 重新灌库（admin） ----------

const togglingDocId = ref('')
const reingestingDocId = ref('')

// 启停文档检索：成功后本地同步开关态；失败回滚列表（以服务端为准，简单可靠）
async function handleToggle(row, target) {
  if (!row || !row.doc_id) return
  togglingDocId.value = row.doc_id
  try {
    await toggleDoc(row.doc_id, target)
    row.enabled = target
    ElMessage.success(target ? '已恢复检索' : '已停用检索')
  } catch (e) {
    // 错误已由 request 拦截器统一提示；开关回滚到服务端状态
    await loadDocs()
  } finally {
    togglingDocId.value = ''
  }
}

// 重新灌库：入队 RQ 异步任务后复用 pollJob 轮询（与上传/重建同模式）
async function handleReingest(row) {
  if (!row || !row.doc_id) return
  reingestingDocId.value = row.doc_id
  try {
    const data = await reingestDoc(row.doc_id)
    const jobId = data.job_id
    if (!jobId) throw new Error('未返回任务 ID')

    await pollJob(jobId, {
      onSuccess: async (job) => {
        const chunks = job.result?.chunks ?? 0
        ElMessage.success(`重灌完成：${chunks} 个 chunk`)
        await loadDocs()
      },
      onFail: (job) => {
        ElMessage.error(`重新灌库失败：${job.error || '未知错误'}`)
      },
    })
  } catch (e) {
    // 入队/轮询失败：request 拦截器已统一提示，这里终止轮询
    console.warn('[Knowledge] 重灌任务终止', e)
  } finally {
    reingestingDocId.value = ''
  }
}

// ---------- 文档 chunk 明细抽屉 ----------

const detailVisible = ref(false)
const detailLoading = ref(false)
const detailTitle = ref('')
const detailChunks = ref(null)
// 打开抽屉时记录该文档的停用态（标题旁展示「已停用」标识）
const detailDisabled = ref(false)

// 存量数据可能缺失 chunk_index（后端按顺序补位，但防御 null/负数）
function chunkIndexLabel(chunk) {
  if (chunk.chunk_index === null || chunk.chunk_index === undefined || chunk.chunk_index < 0) {
    return '·'
  }
  return chunk.chunk_index
}

async function openDetail(row) {
  if (!row || !row.doc_id) return
  detailTitle.value = row.title || row.doc_id
  detailDisabled.value = row?.enabled === false
  detailChunks.value = null
  detailVisible.value = true
  detailLoading.value = true
  try {
    const data = await getDocChunks(row.doc_id)
    detailChunks.value = data.chunks || []
  } catch (e) {
    // 404/503 已由 request 拦截器统一提示，抽屉内显示空态
    detailChunks.value = null
  } finally {
    detailLoading.value = false
  }
}

// ---------- 召回测试（dry-run） ----------

const testQuery = ref('')
const testTopK = ref(5)
const testRunning = ref(false)
const testResult = ref(null)
const testDegraded = ref([])

const fusedHits = computed(() => testResult.value?.fused || [])
const vectorHits = computed(() => testResult.value?.vector || [])
const bm25Hits = computed(() => testResult.value?.bm25 || [])

function fmtScore(score) {
  return Number(score ?? 0).toFixed(4)
}

function summarizeText(text) {
  const t = String(text || '').replace(/\s+/g, ' ').trim()
  return t.length > 100 ? `${t.slice(0, 100)}…` : t
}

async function runSearchTest() {
  const query = testQuery.value.trim()
  if (!query) {
    ElMessage.warning('请输入测试问题')
    return
  }
  testRunning.value = true
  testResult.value = null
  testDegraded.value = []
  try {
    const data = await searchTest({ query, top_k: testTopK.value })
    testResult.value = data
    testDegraded.value = data.degraded || []
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  } finally {
    testRunning.value = false
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
.card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  align-items: center;
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
.degraded-alert {
  margin-bottom: 16px;
}
/* 停用标识（标题列 / 抽屉标题旁） */
.disabled-tag {
  margin-left: 8px;
}
/* 抽屉标题行：标题 + 停用标识 */
.drawer-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}
/* 上传对话框 */
.upload-category {
  margin-top: 12px;
}
/* chunk 明细抽屉 */
.detail-body {
  min-height: 120px;
}
.chunk-title {
  font-size: 13px;
}
.chunk-text {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.7;
  color: var(--am-text-2, inherit);
}
/* 召回测试 */
.test-form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.test-query {
  flex: 1;
  min-width: 240px;
}
.test-section {
  margin-top: 16px;
}
.test-section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.test-empty {
  margin-top: 16px;
}
</style>
