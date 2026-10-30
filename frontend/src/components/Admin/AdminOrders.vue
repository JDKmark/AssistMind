<template>
  <!-- 商城订单：按归属用户/状态筛选，分页查看 -->
  <el-card shadow="never" class="panel-card mall-card">
    <template #header>
      <div class="card-header">
        <span class="card-title">商城订单</span>
        <div class="mall-filters">
          <el-input
            v-model="ownerFilter"
            size="small"
            style="width: 200px"
            placeholder="按用户筛选，如 user1"
            clearable
            @keyup.enter="search"
            @clear="search"
          />
          <el-select
            v-model="statusFilter"
            size="small"
            style="width: 120px"
            placeholder="全部状态"
            clearable
            @change="search"
          >
            <el-option label="待付款" value="待付款" />
            <el-option label="待发货" value="待发货" />
            <el-option label="已发货" value="已发货" />
            <el-option label="已完成" value="已完成" />
          </el-select>
          <el-button size="small" type="primary" :loading="loading" @click="search">查询</el-button>
        </div>
      </div>
    </template>

    <!-- 窄屏容器内横滚（P0-3） -->
    <div class="table-scroll">
      <el-table :data="orders" size="small" border empty-text="暂无订单数据">
        <el-table-column label="订单号" min-width="150">
          <template #default="{ row }">{{ row.order_sn || '-' }}</template>
        </el-table-column>
        <el-table-column label="用户" width="100">
          <template #default="{ row }">{{ row.owner_username || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag
              :type="statusTagType(row.status)"
              size="small"
              effect="plain"
              class="am-tag-pill"
            >
              {{ row.status || '-' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="实付金额(¥)" width="110" align="right">
          <template #default="{ row }">
            <span class="am-mono amount-cell">{{ fmtAmount(row.pay_amount) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="物流单号" min-width="140">
          <template #default="{ row }">{{ row.logistics_no || '-' }}</template>
        </el-table-column>
        <el-table-column label="下单时间" width="150">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <div class="mall-pagination">
      <el-pagination
        v-model:current-page="page"
        :total="total"
        :page-size="PAGE_SIZE"
        layout="total, prev, pager, next"
        @current-change="load"
      />
    </div>
  </el-card>
</template>

<script setup>
/**
 * 管理后台「商城订单」面板。
 *
 * 从 views/Admin/index.vue 抽出：自身持有筛选条件、分页与列表数据，
 * 挂载即加载第一页；后端不可用时静默降级为空列表（错误提示由 request.js 拦截器统一节流）。
 */
import { onMounted, ref } from 'vue'
import { listOrders } from '@/api/mall'
import { fmtTime } from '@/utils/datetime'

const PAGE_SIZE = 10
const STATUS_TAG_TYPES = {
  待付款: 'warning',
  待发货: 'info',
  已发货: 'primary',
  已完成: 'success',
}

const orders = ref([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const ownerFilter = ref('')
const statusFilter = ref('')

function statusTagType(status) {
  return STATUS_TAG_TYPES[status] || 'info'
}

function fmtAmount(amount) {
  const n = Number(amount)
  return Number.isFinite(n) ? n.toFixed(2) : '-'
}

// 请求序号：快速翻页或切筛选时先发的请求可能后返回，只认最后一次，
// 否则慢网络下会出现「页码与表格内容错位」
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const params = {
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    }
    if (ownerFilter.value.trim()) params.owner_username = ownerFilter.value.trim()
    if (statusFilter.value) params.status = statusFilter.value
    const data = await listOrders(params)
    if (seq !== loadSeq) return // 已有更新的请求在途，丢弃本次结果
    orders.value = data.orders || []
    total.value = data.total != null ? data.total : orders.value.length
  } catch {
    if (seq !== loadSeq) return
    // 后端不可用已由拦截器节流提示（页面横幅提示重试）；此处静默降级为空列表
    orders.value = []
    total.value = 0
  } finally {
    // 只有最后一次请求负责收尾 loading，避免旧请求提前关掉新请求的加载态
    if (seq === loadSeq) loading.value = false
  }
}

function search() {
  page.value = 1
  load()
}

onMounted(load)

defineExpose({ load, search })
</script>

<style scoped>
/* 商城订单 */
.mall-card {
  margin-top: 16px;
}
.mall-filters {
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
/* 窄屏：表格容器内横滚，页面本体不溢出（P0-3） */
.table-scroll {
  overflow-x: auto;
}
.table-scroll .el-table {
  min-width: 640px;
}
@media (max-width: 767px) {
  .mall-filters .el-select,
  .mall-filters .el-input {
    width: 100% !important;
  }
  .mall-pagination {
    justify-content: center;
  }
}
</style>
