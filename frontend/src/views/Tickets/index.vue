<template>
  <div class="tickets-page" v-loading="loading">
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

    <el-table :data="tickets" border stripe style="width: 100%">
      <el-table-column prop="id" label="工单ID" width="200" />
      <el-table-column v-if="auth.role !== 'user'" prop="user_id" label="客户" width="110" />
      <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
      <el-table-column label="优先级" width="100">
        <template #default="{ row }">
          <el-tag :type="priorityTagType(row.priority)">{{ priorityLabel(row.priority) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)">{{ statusLabel(row.status) }}</el-tag>
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

    <el-dialog v-model="detailVisible" title="工单详情" width="560px">
      <div v-loading="detailLoading" class="detail-body">
        <el-descriptions v-if="currentTicket" :column="1" border>
          <el-descriptions-item label="标题">{{ currentTicket.title }}</el-descriptions-item>
          <el-descriptions-item label="描述">{{ currentTicket.description }}</el-descriptions-item>
          <el-descriptions-item label="优先级">
            <el-tag :type="priorityTagType(currentTicket.priority)">
              {{ priorityLabel(currentTicket.priority) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="statusTagType(currentTicket.status)">
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
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listTickets, createTicket, updateTicketStatus, getTicket } from '@/api/ticket'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

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
  try {
    const data = await getTicket(row.id)
    currentTicket.value = data
  } catch (e) {
    // 错误已由 request 拦截器统一提示
    currentTicket.value = null
  } finally {
    detailLoading.value = false
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
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.detail-body {
  min-height: 80px;
}
</style>
