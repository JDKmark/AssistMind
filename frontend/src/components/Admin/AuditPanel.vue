<template>
  <el-card shadow="never" class="panel-card">
    <template #header><span class="card-title">操作审计</span></template>
    <el-table :data="auditLogs" size="small" border empty-text="暂无审计记录">
      <el-table-column prop="actor_username" label="操作者" />
      <el-table-column prop="action" label="动作" />
      <el-table-column prop="target_type" label="目标类型" />
      <el-table-column prop="target_id" label="目标" />
      <el-table-column prop="created_at" label="时间" />
    </el-table>
  </el-card>
</template>

<script setup>
/**
 * 操作审计表（只读）：操作者 / 动作 / 目标 / 时间。
 *
 * 从 views/Admin/index.vue 抽出。纯展示，数据由 useAdminUsers 的 loadAdminData 一并拉取。
 */
defineProps({
  auditLogs: { type: Array, default: () => [] },
})
</script>

<style scoped>
.card-title {
  font-weight: 600;
}
</style>
