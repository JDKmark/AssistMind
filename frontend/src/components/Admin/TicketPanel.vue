<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-header">
        <span class="card-title">工单管理</span>
        <div class="ticket-filters">
          <el-select
            v-model="statusModel"
            size="small"
            style="width: 120px"
            placeholder="全部状态"
            clearable
            @change="emit('search')"
          >
            <el-option label="待处理" value="open" />
            <el-option label="处理中" value="in_progress" />
            <el-option label="已解决" value="resolved" />
            <el-option label="已关闭" value="closed" />
          </el-select>
          <el-select
            v-model="priorityModel"
            size="small"
            style="width: 120px"
            placeholder="全部优先级"
            clearable
            @change="emit('search')"
          >
            <el-option label="紧急" value="urgent" />
            <el-option label="高" value="high" />
            <el-option label="中" value="medium" />
            <el-option label="低" value="low" />
          </el-select>
          <el-input
            v-model="customerModel"
            size="small"
            style="width: 180px"
            placeholder="按客户筛选"
            clearable
            @keyup.enter="emit('search')"
            @clear="emit('search')"
          />
          <el-button size="small" type="primary" @click="emit('search')">查询</el-button>
        </div>
      </div>
    </template>
    <!-- 窄屏容器内横滚（P0-3）：页面不横向溢出，表格可滑动查看 -->
    <div class="table-scroll">
      <el-table :data="tickets" size="small" border empty-text="暂无工单">
        <el-table-column prop="id" label="工单号" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="am-mono">{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="user_id" label="客户" width="110" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag
              :type="ticketStatusTagType(row.status)"
              size="small"
              effect="plain"
              class="am-tag-pill"
            >
              {{ ticketStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="优先级" width="90">
          <template #default="{ row }">
            <el-tag
              :type="priorityTagType(row.priority)"
              size="small"
              effect="plain"
              class="am-tag-pill"
            >
              {{ priorityLabel(row.priority) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="emit('open-detail', row)">
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </el-card>
</template>

<script setup>
/**
 * 工单管理面板：状态/优先级/客户筛选 + 工单表。
 *
 * 从 views/Admin/index.vue 抽出。查询在 composables/useAdminTickets.js，
 * 筛选值用 v-model 双向绑定回页面（三个筛选条件与查询动作分属两处状态）。
 */
import { computed } from 'vue'
import {
  ticketStatusLabel,
  ticketStatusTagType,
  priorityLabel,
  priorityTagType,
} from '@/utils/labels'

const props = defineProps({
  tickets: { type: Array, default: () => [] },
  statusFilter: { type: String, default: '' },
  priorityFilter: { type: String, default: '' },
  customerFilter: { type: String, default: '' },
})

const emit = defineEmits([
  'update:statusFilter',
  'update:priorityFilter',
  'update:customerFilter',
  'search',
  'open-detail',
])

const statusModel = computed({
  get: () => props.statusFilter,
  set: (v) => emit('update:statusFilter', v),
})
const priorityModel = computed({
  get: () => props.priorityFilter,
  set: (v) => emit('update:priorityFilter', v),
})
const customerModel = computed({
  get: () => props.customerFilter,
  set: (v) => emit('update:customerFilter', v),
})
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title {
  font-weight: 600;
}

/* 工单筛选 */
.ticket-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
/* 窄屏：表格容器内横滚，页面本体不溢出（P0-3） */
.table-scroll {
  overflow-x: auto;
}
.table-scroll .el-table {
  min-width: 640px;
}

@media (max-width: 767px) {
  .ticket-filters .el-select,
  .ticket-filters .el-input {
    width: 100% !important;
  }
}
</style>
