<template>
  <el-card shadow="never" class="panel-card">
    <template #header><span class="card-title">用户管理</span></template>
    <el-table :data="users" size="small" border empty-text="暂无用户">
      <el-table-column prop="username" label="用户名" />
      <el-table-column label="角色" width="120">
        <template #default="{ row }">
          <el-tag :type="roleTagType(row.role)" size="small" effect="plain" class="am-tag-pill">
            {{ roleLabel(row.role) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态">
        <template #default="{ row }">{{ row.is_active ? '启用' : '停用' }}</template>
      </el-table-column>
      <el-table-column label="操作">
        <template #default="{ row }">
          <el-button
            v-if="row.role === 'user'"
            size="small"
            @click="emit('change-role', row, 'agent')"
            >设为客服</el-button
          >
          <el-button
            v-else-if="row.role === 'agent'"
            size="small"
            @click="emit('change-role', row, 'user')"
            >设为用户</el-button
          >
          <el-popconfirm
            v-if="row.role !== 'admin'"
            :title="
              row.is_active ? `确认停用用户 ${row.username}？` : `确认启用用户 ${row.username}？`
            "
            width="240"
            @confirm="emit('toggle', row)"
          >
            <template #reference>
              <el-button size="small" :type="row.is_active ? 'danger' : 'success'">
                {{ row.is_active ? '停用' : '启用' }}
              </el-button>
            </template>
          </el-popconfirm>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
/**
 * 用户管理表：角色变更（用户 ↔ 客服）与启停。
 *
 * 从 views/Admin/index.vue 抽出。请求与刷新在 composables/useAdminUsers.js，
 * 本组件只把两个动作抛回页面（管理员自身不可停用，故按钮按角色条件渲染）。
 */
import { roleLabel, roleTagType } from '@/utils/labels'

defineProps({
  users: { type: Array, default: () => [] },
})

const emit = defineEmits(['change-role', 'toggle'])
</script>

<style scoped>
.card-title {
  font-weight: 600;
}
</style>
