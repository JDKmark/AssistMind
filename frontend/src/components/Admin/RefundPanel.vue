<template>
  <el-card shadow="never" class="panel-card">
    <template #header><span class="card-title">退款处理</span></template>
    <el-table :data="refunds" size="small" border empty-text="暂无退款">
      <el-table-column prop="refund_id" label="退款单" />
      <el-table-column prop="order_sn" label="订单号" />
      <el-table-column prop="owner_username" label="用户" />
      <el-table-column prop="status" label="状态" width="96">
        <template #default="{ row }">
          <el-tag size="small" type="info" effect="plain" class="am-tag-pill">
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作">
        <template #default="{ row }">
          <template v-if="row.status === '处理中'">
            <el-button size="small" type="success" @click="emit('change-status', row, '已通过')"
              >通过</el-button
            >
            <el-button size="small" type="danger" @click="emit('change-status', row, '已拒绝')"
              >拒绝</el-button
            >
          </template>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
/**
 * 退款处理表：处理中的退款单可通过 / 拒绝。
 *
 * 从 views/Admin/index.vue 抽出。状态流转与列表刷新在
 * composables/useAdminRefunds.js，本组件只把动作抛回页面。
 */
defineProps({
  refunds: { type: Array, default: () => [] },
})

const emit = defineEmits(['change-status'])
</script>

<style scoped>
.card-title {
  font-weight: 600;
}
</style>
