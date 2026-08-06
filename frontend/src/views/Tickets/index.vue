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
      <el-table-column label="操作" width="170">
        <template #default="{ row }">
          <el-select
            v-model="row.status"
            size="small"
            style="width: 140px"
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
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listTickets, createTicket, updateTicketStatus } from '@/api/ticket'

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
  padding: 16px;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
</style>
