<template>
  <div class="orders-page" v-loading="loading">
    <div class="toolbar">
      <el-select
        v-model="filterStatus"
        placeholder="按状态筛选"
        clearable
        style="width: 180px"
        @change="search"
      >
        <el-option label="待付款" value="待付款" />
        <el-option label="待发货" value="待发货" />
        <el-option label="已发货" value="已发货" />
        <el-option label="已完成" value="已完成" />
      </el-select>
    </div>

    <el-table :data="orders" border stripe style="width: 100%">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="items-box">
            <div v-for="(item, i) in row.items || []" :key="i" class="item-line">
              <span class="item-name">{{ item.name }}</span>
              <span class="item-spec">{{ item.spec || '-' }}</span>
              <span class="item-price">¥{{ fmtAmount(item.price) }}</span>
              <span class="item-qty">×{{ item.quantity }}</span>
            </div>
            <div v-if="!(row.items && row.items.length)" class="empty-items">暂无商品明细</div>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="order_sn" label="订单号" min-width="170" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="实付金额" width="110">
        <template #default="{ row }">¥{{ fmtAmount(row.pay_amount) }}</template>
      </el-table-column>
      <el-table-column label="物流单号" min-width="150">
        <template #default="{ row }">{{ row.logistics_no || '-' }}</template>
      </el-table-column>
      <el-table-column label="下单时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
    </el-table>

    <div class="pagination-wrap">
      <el-pagination
        v-model:current-page="page"
        :total="total"
        :page-size="PAGE_SIZE"
        layout="total, prev, pager, next"
        @current-change="loadOrders"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listMyOrders } from '@/api/mall'

const PAGE_SIZE = 10

const loading = ref(false)
const orders = ref([])
const total = ref(0)
const page = ref(1)
const filterStatus = ref('')

const STATUS_TAG_TYPES = {
  待付款: 'warning',
  待发货: 'info',
  已发货: 'primary',
  已完成: 'success',
}

function statusTagType(s) {
  return STATUS_TAG_TYPES[s] || 'info'
}

function fmtAmount(amount) {
  const n = Number(amount)
  return Number.isFinite(n) ? n.toFixed(2) : '-'
}

function formatTime(iso) {
  if (!iso) return ''
  return String(iso).replace('T', ' ').slice(0, 19)
}

async function loadOrders() {
  loading.value = true
  try {
    const params = {
      limit: PAGE_SIZE,
      offset: (page.value - 1) * PAGE_SIZE,
    }
    if (filterStatus.value) params.status = filterStatus.value
    const data = await listMyOrders(params)
    orders.value = data.orders || []
    total.value = data.total != null ? data.total : orders.value.length
  } catch (e) {
    orders.value = []
    total.value = 0
    ElMessage.error('我的订单加载失败')
  } finally {
    loading.value = false
  }
}

function search() {
  page.value = 1
  loadOrders()
}

onMounted(loadOrders)
</script>

<style scoped>
.orders-page {
  padding: 20px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.pagination-wrap {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
.items-box {
  padding: 8px 16px;
}
.item-line {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 4px 0;
  font-size: 13px;
}
.item-name {
  min-width: 180px;
  font-weight: 500;
  color: var(--am-text, #303133);
}
.item-spec {
  min-width: 120px;
  color: var(--am-text-3, #909399);
}
.item-price {
  min-width: 80px;
  color: var(--am-text-2, #606266);
}
.item-qty {
  min-width: 48px;
  color: var(--am-text-3, #909399);
}
.empty-items {
  color: var(--am-text-3, #909399);
  font-size: 13px;
  padding: 4px 0;
}
</style>
