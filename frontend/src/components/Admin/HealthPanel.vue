<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <PanelHeader title="系统状态">
        <el-tag :type="overallTagType" size="small" effect="plain">
          {{ overallLabel }}
        </el-tag>
      </PanelHeader>
    </template>
    <el-table :data="depRows" size="small" border style="width: 100%">
      <el-table-column prop="label" label="组件" width="140" />
      <el-table-column label="状态">
        <template #default="{ row }">
          <el-tag :type="row && row.tagType" size="small" effect="plain" class="am-tag-pill">
            {{ row && row.statusText }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<script setup>
import PanelHeader from '@/components/Admin/PanelHeader.vue'
/**
 * 系统状态面板：四个依赖的连通状态 + 整体结论。
 *
 * 从 views/Admin/index.vue 抽出。状态映射在 composables/useAdminHealth.js，
 * 本组件只负责渲染（依赖顺序即后端约定的固定四件套）。
 */
defineProps({
  depRows: { type: Array, required: true },
  overallLabel: { type: String, default: '' },
  overallTagType: { type: String, default: '' },
})
</script>

<style scoped></style>
