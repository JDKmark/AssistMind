<template>
  <el-card shadow="never" class="panel-card">
    <template #header>
      <div class="card-header">
        <span class="card-title">数据概览</span>
        <span v-if="dataLoading" class="muted-text">加载中…</span>
      </div>
    </template>

    <!-- 指标卡：大数字 + 灰标 + 状态点（Stripe 式数据呈现） -->
    <div class="metric-grid">
      <div class="metric">
        <div class="metric-label">文档数</div>
        <div class="metric-value am-mono">{{ kbTotal }}</div>
        <div class="metric-sub">知识库</div>
      </div>
      <div class="metric">
        <div class="metric-label">Chunk 数</div>
        <div class="metric-value am-mono">{{ kbChunks }}</div>
        <div class="metric-sub">检索单元</div>
      </div>
      <div class="metric">
        <div class="metric-label">工单总数</div>
        <div class="metric-value am-mono">{{ ticketTotal }}</div>
        <div class="metric-sub">全部工单</div>
      </div>
      <div class="metric">
        <div class="metric-label">状态分布</div>
        <div class="metric-chips">
          <span v-for="(count, st) in statusDist" :key="st" class="mchip">
            {{ statusLabel(st) }} {{ count }}
          </span>
        </div>
        <div class="metric-sub">待处理 · 已解决</div>
      </div>
    </div>
    <div v-if="kbError" class="muted-text">{{ kbError }}</div>
  </el-card>
</template>

<script setup>
/**
 * 数据概览卡：文档数 / Chunk 数 / 工单总数 / 状态分布（Stripe 式指标卡）。
 *
 * 从 views/Admin/index.vue 抽出。指标来自两处数据源（知识库统计 + 工单快照），
 * 由页面装配后以 props 下传，组件不自己发请求。
 */
import { statusLabel } from '@/utils/adminLabels'

defineProps({
  kbTotal: { type: Number, default: 0 },
  kbChunks: { type: Number, default: 0 },
  ticketTotal: { type: Number, default: 0 },
  statusDist: { type: Object, required: true },
  kbError: { type: String, default: '' },
  dataLoading: { type: Boolean, default: false },
})
</script>

<style scoped>
.muted-text {
  color: var(--am-text-3);
  font-size: 13px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title {
  font-weight: 600;
}

/* 指标卡网格（Stripe 式数据呈现） */
.metric-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.metric {
  padding: 8px 0;
}
.metric-label {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--am-text-3);
  margin-bottom: 4px;
}
.metric-value {
  font-size: 26px;
  font-weight: 700;
  color: var(--am-text);
  line-height: 1.2;
  margin-bottom: 2px;
}
.metric-sub {
  font-size: 12px;
  color: var(--am-text-3);
}
.metric-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 2px;
}
.mchip {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  font-size: 11px;
  font-weight: 500;
  border-radius: 999px;
  background: var(--am-blue-50);
  color: var(--am-text-2);
}

@media (max-width: 767px) {
  .metric-grid {
    grid-template-columns: 1fr;
  }
}
</style>
