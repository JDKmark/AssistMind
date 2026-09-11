<template>
  <div class="tickets-page" v-loading="loading">
    <div class="am-table-wrap">
      <div class="toolbar">
        <el-select
          v-model="filterStatus"
          placeholder="按状态过滤"
          clearable
          style="width: 180px"
          @change="loadTickets"
        >
          <el-option label="全部" value="" />
          <el-option label="待处理" value="open" />
          <el-option label="处理中" value="in_progress" />
          <el-option label="已解决" value="resolved" />
          <el-option label="已关闭" value="closed" />
        </el-select>
        <el-button type="primary" @click="openCreateDialog">创建工单</el-button>
      </div>

    <!-- 窄屏容器内横滚（P0-3）：页面不横向溢出，表格可滑动查看 -->
    <div class="table-scroll">
    <el-table :data="tickets" style="width: 100%">
      <el-table-column prop="id" label="工单ID" width="175" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="am-mono ticket-id">{{ row.id }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="auth.role !== 'user'" prop="user_id" label="客户" width="110" />
      <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
      <el-table-column label="优先级" width="110">
        <template #default="{ row }">
          <el-tag :type="priorityTagType(row.priority)" size="small" effect="plain" class="am-tag-pill">
            {{ priorityLabel(row.priority) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small" effect="plain" class="am-tag-pill">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="250">
        <template #default="{ row }">
          <el-button size="small" type="primary" link @click="openDetail(row)">详情</el-button>
          <el-button
            v-if="auth.role === 'user' && row.status === 'resolved'"
            type="primary"
            size="small"
            @click="confirmResolve(row)"
          >
            确认解决
          </el-button>
          <el-select
            v-if="auth.role !== 'user'"
            v-model="row.status"
            size="small"
            style="width: 140px; margin-left: 8px"
            @change="(val) => handleStatusChange(row, val)"
          >
            <el-option label="待处理" value="open" />
            <el-option label="处理中" value="in_progress" />
            <el-option label="已解决" value="resolved" />
            <el-option label="已关闭" value="closed" />
          </el-select>
        </template>
      </el-table-column>
    </el-table>
    </div>
    </div>

    <el-dialog v-model="dialogVisible" title="创建工单" width="560px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px">
        <el-form-item label="标题" prop="title">
          <el-input v-model="form.title" placeholder="请输入工单标题" maxlength="200" />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="4"
            placeholder="请描述问题"
          />
        </el-form-item>
        <el-form-item label="优先级" prop="priority">
          <el-select v-model="form.priority" style="width: 100%">
            <el-option label="低" value="low" />
            <el-option label="普通" value="normal" />
            <el-option label="高" value="high" />
            <el-option label="紧急" value="urgent" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="detailVisible"
      title="工单详情"
      :size="isMobile ? '100%' : '560px'"
    >
      <div v-loading="detailLoading" class="detail-body">
        <el-descriptions v-if="currentTicket" :column="1" border>
          <el-descriptions-item label="工单ID">
            <span class="am-mono ticket-id-text">{{ currentTicket.id }}</span>
            <el-button link type="primary" size="small" class="copy-id-btn" @click="copyTicketId">
              复制
            </el-button>
          </el-descriptions-item>
          <el-descriptions-item label="标题">{{ currentTicket.title }}</el-descriptions-item>
          <el-descriptions-item label="描述">{{ currentTicket.description }}</el-descriptions-item>
          <el-descriptions-item label="优先级">
            <el-tag :type="priorityTagType(currentTicket.priority)" size="small" effect="plain" class="am-tag-pill">
              {{ priorityLabel(currentTicket.priority) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusTagType(currentTicket.status)" size="small" effect="plain" class="am-tag-pill">
              {{ statusLabel(currentTicket.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">
            {{ formatTime(currentTicket.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="更新时间">
            {{ formatTime(currentTicket.updated_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <!-- 人工介入沟通线程：用户补充说明，客服/管理员答复，双方可见 -->
        <div class="reply-section">
          <div class="reply-title">沟通记录</div>
          <div v-if="replies.length" class="reply-list">
            <div
              v-for="r in replies"
              :key="r.id"
              class="reply-item"
              :class="r.sender_role === 'user' ? 'reply-self' : ''"
            >
              <div class="reply-meta">
                <el-tag size="small" :type="replySenderTagType(r.sender_role)" effect="plain">
                  {{ replySenderLabel(r.sender_role, r.sender_username) }}
                </el-tag>
                <span class="reply-time">{{ formatTime(r.created_at) }}</span>
              </div>
              <div class="reply-content">{{ r.content }}</div>
            </div>
          </div>
          <div v-else class="muted-text reply-empty">暂无沟通记录，发送回复后双方可见</div>

          <div class="reply-input-row">
            <el-input
              v-model="replyDraft"
              type="textarea"
              :rows="2"
              maxlength="2000"
              :placeholder="auth.role === 'user'
                ? '补充说明或回复客服…'
                : '以人工身份回复（open 工单将自动转为处理中）'"
            />
            <el-button
              type="primary"
              :loading="replySubmitting"
              :disabled="!replyDraft.trim()"
              @click="sendReply"
            >
              发送回复
            </el-button>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listTickets, createTicket, updateTicketStatus, getTicket, listReplies, addReply } from '@/api/ticket'
import { useAuthStore } from '@/stores/auth'
import { useBreakpoint } from '@/utils/useBreakpoint'

const auth = useAuthStore()
// 移动端抽屉全宽（P0-2 同款处理）
const { isMobile } = useBreakpoint()

const loading = ref(false)
const tickets = ref([])
const filterStatus = ref('')

const dialogVisible = ref(false)
const submitting = ref(false)
const formRef = ref(null)
const form = ref({
  title: '',
  description: '',
  priority: 'normal',
})

// 工单详情弹窗
const detailVisible = ref(false)
const detailLoading = ref(false)
const currentTicket = ref(null)
// 人工介入沟通线程
const replies = ref([])
const replyDraft = ref('')
const replySubmitting = ref(false)

function replySenderTagType(role) {
  return role === 'user' ? 'info' : 'warning'
}
function replySenderLabel(role, username) {
  const who = role === 'admin' ? '管理员' : role === 'agent' ? '客服' : '用户'
  return `${who} ${username}`
}

const rules = {
  title: [{ required: true, message: '请输入标题', trigger: 'blur' }],
  description: [{ required: true, message: '请输入描述', trigger: 'blur' }],
}

const STATUS_LABELS = {
  open: '待处理',
  in_progress: '处理中',
  resolved: '已解决',
  closed: '已关闭',
}
const PRIORITY_LABELS = {
  low: '低',
  normal: '普通',
  high: '高',
  urgent: '紧急',
}

function statusLabel(s) {
  return STATUS_LABELS[s] || s
}
function priorityLabel(p) {
  return PRIORITY_LABELS[p] || p
}
function statusTagType(s) {
  return { open: 'info', in_progress: 'warning', resolved: 'success', closed: '' }[s] || ''
}
function priorityTagType(p) {
  return { low: 'info', normal: '', high: 'warning', urgent: 'danger' }[p] || ''
}
function formatTime(iso) {
  if (!iso) return ''
  return iso.replace('T', ' ').slice(0, 19)
}

// 复制工单号到剪贴板（详情抽屉内小工具；失败提示不静默）
async function copyTicketId() {
  const id = currentTicket.value && currentTicket.value.id
  if (!id) return
  try {
    await navigator.clipboard.writeText(id)
    ElMessage.success('工单号已复制')
  } catch (e) {
    console.warn('[Tickets] 工单号复制失败', e)
    ElMessage.warning('复制失败，请手动选择工单号')
  }
}

async function loadTickets() {
  loading.value = true
  try {
    const data = await listTickets(filterStatus.value)
    tickets.value = data.tickets || []
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  } finally {
    loading.value = false
  }
}

async function handleStatusChange(row, newStatus) {
  try {
    await updateTicketStatus(row.id, newStatus)
    ElMessage.success(`状态已更新为 ${statusLabel(newStatus)}`)
  } catch (e) {
    // 非法状态流转或权限不足：重新加载恢复服务器真实状态（错误提示由 request 拦截器处理）
    await loadTickets()
  }
}

async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  currentTicket.value = null
  replies.value = []
  replyDraft.value = ''
  try {
    const [data, replyData] = await Promise.all([getTicket(row.id), listReplies(row.id)])
    currentTicket.value = data
    replies.value = replyData.replies || []
  } catch (e) {
    // 错误已由 request 拦截器统一提示
    currentTicket.value = null
  } finally {
    detailLoading.value = false
  }
}

async function sendReply() {
  const content = replyDraft.value.trim()
  if (!content || !currentTicket.value) return
  replySubmitting.value = true
  try {
    await addReply(currentTicket.value.id, content)
    ElMessage.success('回复已发送')
    replyDraft.value = ''
    const replyData = await listReplies(currentTicket.value.id)
    replies.value = replyData.replies || []
    // 同步弹窗内状态显示；staff 回复 open 工单后端已自动转 in_progress，列表同步刷新
    if (auth.role !== 'user' && currentTicket.value.status === 'open') {
      currentTicket.value.status = 'in_progress'
    }
    await loadTickets()
  } catch (e) {
    console.warn('[Tickets] 回复发送失败', e)
  } finally {
    replySubmitting.value = false
  }
}

async function confirmResolve(row) {
  try {
    await updateTicketStatus(row.id, 'closed')
    ElMessage.success('已确认解决，工单关闭')
    await loadTickets()
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  }
}

function openCreateDialog() {
  form.value = { title: '', description: '', priority: 'normal' }
  dialogVisible.value = true
}

async function submitCreate() {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  submitting.value = true
  try {
    await createTicket(form.value)
    ElMessage.success('工单创建成功')
    dialogVisible.value = false
    await loadTickets()
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  } finally {
    submitting.value = false
  }
}

onMounted(loadTickets)
</script>

<style scoped>
.tickets-page {
  padding: 20px;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 8px 12px;
  margin-bottom: 14px;
}
/* 窄屏：表格容器内横滚，页面本体不溢出（P0-3） */
.table-scroll {
  overflow-x: auto;
}
.table-scroll .el-table {
  min-width: 880px;
}
@media (max-width: 767px) {
  .toolbar .el-select {
    width: 100% !important;
  }
}
.detail-body {
  min-height: 80px;
}
.ticket-id {
  cursor: default;
}
.ticket-id-text {
  font-family: var(--am-font-mono);
  font-size: 13px;
  word-break: break-all;
}
.copy-id-btn {
  margin-left: 6px;
}
.reply-section {
  margin-top: 16px;
}
.reply-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--am-text-2);
  margin-bottom: 8px;
}
.reply-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 260px;
  overflow-y: auto;
  margin-bottom: 12px;
}
/* 沟通线程：用户回复右对齐蓝泡，客服/管理员左对齐白卡 */
.reply-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-self: flex-start;
  max-width: 86%;
  padding: 8px 12px;
  background: #fff;
  border: 1px solid var(--am-line);
  border-radius: 10px;
  border-top-left-radius: 3px;
}
.reply-item.reply-self {
  align-self: flex-end;
  background: linear-gradient(135deg, var(--am-blue-600), var(--am-blue-500));
  border: none;
  border-top-right-radius: 3px;
}
.reply-item.reply-self .reply-meta,
.reply-item.reply-self .reply-time,
.reply-item.reply-self .reply-content {
  color: rgba(255, 255, 255, 0.88);
}
.reply-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}
.reply-item.reply-self :deep(.el-tag) {
  --el-tag-text-color: rgba(255, 255, 255, 0.9) !important;
  --el-tag-bg-color: rgba(255, 255, 255, 0.14) !important;
  --el-tag-border-color: rgba(255, 255, 255, 0.24) !important;
}
.reply-time {
  font-size: 12px;
  color: var(--am-text-3);
}
.reply-content {
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.reply-empty {
  margin-bottom: 12px;
}
.reply-input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}
/* 移动端：详情抽屉内的输入行纵向堆叠（P0-3 配套） */
@media (max-width: 767px) {
  .reply-input-row {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
