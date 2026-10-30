<template>
  <!-- 会话追溯时间线抽屉（步骤可视化：意图 → 检索 → 决策 → 回答 → 证据链） -->
  <el-drawer v-model="visibleModel" title="会话追溯" :size="isMobile ? '100%' : '560px'">
    <div v-if="trace" class="trace-body">
      <div class="trace-question">
        <div class="overview-title">问题</div>
        <div>{{ trace.query || '-' }}</div>
        <div class="muted-text">评分 {{ trace.score }} · {{ fmtTime(trace.created_at) }}</div>
      </div>

      <div class="timeline">
        <div class="trace-step">
          <div class="step-label">① 意图路由</div>
          <el-tag size="small" effect="plain">{{ intentLabel(trace.intent) }}</el-tag>
        </div>

        <div class="trace-step">
          <div class="step-label">② 检索来源（{{ (trace.sources || []).length }} 条）</div>
          <div v-if="trace.sources && trace.sources.length">
            <div v-for="(s, i) in trace.sources" :key="i" class="source-block">
              <div class="source-line">
                [{{ i + 1 }}] {{ s.title || s.doc_id || '知识库命中' }}
                <span v-if="s.score !== undefined && s.score !== null" class="source-score">
                  {{ fmtScore(s.score) }}
                </span>
              </div>
              <div class="source-path">{{ s.source }}</div>
              <div class="source-text">{{ s.text || s.snippet || '' }}</div>
            </div>
          </div>
          <div v-else class="muted-text">无检索来源（未命中）</div>
        </div>

        <div class="trace-step">
          <div class="step-label">③ CRAG 决策</div>
          <el-tag size="small" :type="cragTagType">{{ cragLabel }}</el-tag>
          <el-tag
            v-for="(d, i) in trace.degraded || []"
            :key="i"
            size="small"
            type="warning"
            effect="plain"
            class="service-tag"
          >
            降级: {{ d }}
          </el-tag>
        </div>

        <div class="trace-step">
          <div class="step-label">④ 回答</div>
          <div class="trace-answer">{{ trace.answer || '（无回答）' }}</div>
        </div>

        <div v-if="trace.trace_id" class="trace-step">
          <div class="step-label">⑤ 证据链</div>
          <a
            v-if="langfuseHost && trace.trace_id"
            :href="langfuseUrl(trace.trace_id)"
            target="_blank"
            rel="noopener"
            class="trace-link"
          >
            在 Langfuse 查看完整 trace（{{ trace.trace_id }}）
          </a>
          <span v-else class="muted-text">trace_id: {{ trace.trace_id }}</span>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
/**
 * 会话追溯抽屉：意图 → 检索来源 → CRAG 决策 → 回答 → 证据链（Langfuse）。
 *
 * 从 views/Admin/index.vue 抽出。被追溯对象与 CRAG 结论由页面计算后下传
 * （cragLabel / cragTagType 依赖当前追溯对象，属状态而非纯格式化）。
 */
import { computed } from 'vue'
import { fmtTime } from '@/utils/datetime'
import { fmtScore, intentLabel } from '@/utils/adminLabels'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  isMobile: { type: Boolean, default: false },
  trace: { type: Object, default: null },
  cragLabel: { type: String, default: '-' },
  cragTagType: { type: String, default: 'success' },
  // Langfuse 控制台地址（空则不渲染深链，退化为纯文本 trace_id）
  langfuseHost: { type: String, default: '' },
  // 深链拼装（host 由接口下发，拼装实现留在页面侧的 useAdminFeedback，避免两份口径）
  langfuseUrl: { type: Function, required: true },
})

const emit = defineEmits(['update:modelValue'])

const visibleModel = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})
</script>

<style scoped>
.muted-text {
  color: var(--am-text-3);
  font-size: 13px;
}
.overview-title {
  font-weight: 600;
  color: var(--am-text-2);
  font-size: 13px;
  margin-bottom: 8px;
}

.trace-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.trace-question {
  padding: 10px 12px;
  background: var(--am-paper);
  border-radius: 6px;
  font-size: 14px;
  line-height: 1.6;
}
.timeline {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.trace-step {
  padding-left: 12px;
  border-left: 2px solid var(--am-blue-200);
}
.step-label {
  font-weight: 600;
  color: var(--am-text);
  font-size: 13px;
  margin-bottom: 6px;
}
.source-block {
  margin-bottom: 8px;
  padding: 8px;
  background: var(--am-paper);
  border-radius: 6px;
}
.source-line {
  font-weight: 600;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.source-score {
  color: var(--am-blue-600);
  font-size: 12px;
}
.source-path {
  color: var(--am-text-3);
  font-size: 12px;
  margin: 2px 0 4px;
  word-break: break-all;
}
.source-text {
  font-size: 13px;
  line-height: 1.6;
  color: var(--am-text-2);
  white-space: pre-wrap;
  word-break: break-all;
}
.trace-answer {
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
}
.trace-link {
  color: var(--am-blue-600);
  text-decoration: none;
  font-size: 13px;
}
.trace-link:hover {
  text-decoration: underline;
}
.service-tag {
  margin-left: 6px;
}
</style>
