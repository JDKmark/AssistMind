<template>
  <!-- 反馈与追溯（Bad Case 归因）：差评列表 → 会话时间线抽屉，借鉴 agent harness 的步骤可视化 -->
  <el-card shadow="never" class="panel-card feedback-card">
    <template #header>
      <PanelHeader title="反馈与追溯">
        <div class="feedback-filters">
          <el-select
            v-model="scoreModel"
            size="small"
            style="width: 112px"
            @change="emit('refresh')"
          >
            <el-option label="全部评分" :value="0" />
            <el-option label="差评 1-2" :value="2" />
            <el-option label="好评 4-5" :value="4" />
          </el-select>
          <el-select
            v-model="exportedModel"
            size="small"
            style="width: 128px"
            @change="emit('refresh')"
          >
            <el-option label="全部状态" value="all" />
            <el-option label="已回流评估集" value="exported" />
            <el-option label="待回流" value="pending" />
          </el-select>
          <el-button size="small" :loading="loading" @click="emit('refresh')">刷新</el-button>
          <el-divider direction="vertical" />
          <EvalPanel :running="evalRunning" :status-text="evalStatusText" @run="emit('run-eval')" />
        </div>
      </PanelHeader>
    </template>

    <!-- 窄屏容器内横滚（P0-3） -->
    <div class="table-scroll">
      <el-table :data="feedbacks" size="small" border empty-text="暂无反馈数据">
        <el-table-column label="评分" width="70">
          <template #default="{ row }">
            <el-tag
              :type="row.score <= 2 ? 'danger' : 'success'"
              size="small"
              effect="plain"
              class="am-tag-pill"
            >
              {{ row.score }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="问题" min-width="170">
          <template #default="{ row }">{{ row.query || '-' }}</template>
        </el-table-column>
        <el-table-column label="回答摘要" min-width="170">
          <template #default="{ row }">{{ (row.answer || '').slice(0, 40) }}</template>
        </el-table-column>
        <el-table-column label="来源数" width="70">
          <template #default="{ row }">{{ (row.sources || []).length }}</template>
        </el-table-column>
        <el-table-column label="时间" width="104">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="emit('open-trace', row)"
              >追溯</el-button
            >
          </template>
        </el-table-column>
      </el-table>
    </div>
    <div v-if="error" class="muted-text">{{ fbError }}</div>
  </el-card>
</template>

<script setup>
/**
 * 反馈与追溯面板：差评筛选（评分 / 回流状态）+ 反馈表 + 评估入口。
 *
 * 从 views/Admin/index.vue 抽出。筛选值与列表在 composables/useAdminFeedback.js，
 * 评估入口单独抽成 EvalPanel（header 内的两个并列控件）。
 */
import { computed } from 'vue'
import PanelHeader from '@/components/Admin/PanelHeader.vue'
import EvalPanel from '@/components/Admin/EvalPanel.vue'
import { fmtTime } from '@/utils/datetime'

const props = defineProps({
  feedbacks: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  scoreFilter: { type: Number, default: 0 },
  exportedFilter: { type: String, default: 'all' },
  evalRunning: { type: Boolean, default: false },
  evalStatusText: { type: String, default: '' },
})

const emit = defineEmits([
  'update:scoreFilter',
  'update:exportedFilter',
  'refresh',
  'run-eval',
  'open-trace',
])

const scoreModel = computed({
  get: () => props.scoreFilter,
  set: (v) => emit('update:scoreFilter', v),
})
const exportedModel = computed({
  get: () => props.exportedFilter,
  set: (v) => emit('update:exportedFilter', v),
})
</script>

<style scoped>
.muted-text {
  color: var(--am-text-3);
  font-size: 13px;
}

/* 反馈与追溯 */
.feedback-filters {
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
  .feedback-filters .el-select {
    width: 100% !important;
  }
}
</style>
