<template>
  <div class="diag-section">
    <button
      type="button"
      class="diag-header"
      :class="{ open: m.diagOpen }"
      @click="m.diagOpen = !m.diagOpen"
    >
      <svg
        class="diag-caret"
        viewBox="0 0 24 24"
        width="12"
        height="12"
        fill="none"
        stroke="currentColor"
        stroke-width="2.5"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M9 6l6 6-6 6" />
      </svg>
      <span class="diag-title">诊断信息</span>
      <span class="diag-chips">
        <span v-if="m.status !== 'done' && m.status !== 'error'" class="diag-chip chip-live">
          {{ stageText(m) }} {{ fmtMs(m.elapsedMs) }}
        </span>
        <span class="diag-chip chip-intent">
          {{ intentLabel(m.intent)
          }}<template v-if="m.routeSource"> · {{ routeLabel(m.routeSource) }}</template>
        </span>
        <span v-if="m.elapsedMs" class="diag-chip">{{ fmtMs(m.elapsedMs) }}</span>
        <span v-if="m.fromCache" class="diag-chip chip-cache">缓存 {{ m.fromCache }}</span>
        <span v-if="m.cragAction" class="diag-chip">{{ cragLabel(m.cragAction) }}</span>
        <span v-if="m.cragScore != null && m.cragScore !== ''" class="diag-chip chip-crag">
          CRAG {{ m.cragScore }}
        </span>
        <span v-if="m.degraded.length" class="diag-chip chip-degraded">
          降级：{{ m.degraded.join('、') }}
        </span>
        <span v-if="m.disambiguation" class="diag-chip chip-disambig">
          消歧 {{ m.disambiguation.status
          }}<template v-if="m.disambiguation.method"> · {{ m.disambiguation.method }}</template>
        </span>
      </span>
      <span class="diag-toggle-text">{{ m.diagOpen ? '收起' : '展开' }}</span>
    </button>
    <div v-show="m.diagOpen" class="diag-body">
      <!-- 会话上下文：确认看到的是谁的问题、什么权限/语气在生效 -->
      <div class="diag-row">
        <span class="diag-row-label">当前用户</span>
        <code class="diag-code">{{ authStore.user?.username || '—' }}</code>
        <el-tag
          size="small"
          effect="plain"
          :type="authStore.role === 'admin' ? 'danger' : 'warning'"
        >
          {{ authStore.role || '—' }}
        </el-tag>
        <span v-if="personaName" class="diag-chip">语气: {{ personaName }}</span>
      </div>

      <!-- 后端精确阶段耗时（faq：检索/生成/总耗时；其余意图仅前端总耗时） -->
      <div v-if="stages.length" class="diag-block">
        <div class="diag-caption">
          阶段耗时{{ hasBackendTimings(m) ? '（后端计时）' : '（前端由事件间隔推断）' }}
        </div>
        <div v-for="row in stages" :key="row[0]" class="diag-stage-row">
          <span class="diag-stage-name">{{ row[0] }}</span>
          <span class="diag-stage-val">{{ fmtMs(row[1]) }}</span>
        </div>
      </div>

      <!-- 查询改写变体（CRAG 被动改写实际用于二次检索的词） -->
      <div v-if="m.rewrites && m.rewrites.length" class="diag-block">
        <div class="diag-caption">查询改写变体（二次检索用）</div>
        <div v-for="(r, i) in m.rewrites" :key="i" class="diag-line">
          <span class="diag-line-idx">{{ i + 1 }}</span>
          <span class="diag-line-text">{{ r }}</span>
        </div>
      </div>

      <!-- 产品消歧决策（phase15）：status/method 与定向商品或候选明细 -->
      <div v-if="m.disambiguation" class="diag-block">
        <div class="diag-caption">产品消歧决策</div>
        <div class="diag-line">
          <span class="diag-line-name">决策</span>
          <span class="diag-line-text">
            {{ m.disambiguation.status
            }}<template v-if="m.disambiguation.method"> · {{ m.disambiguation.method }}</template>
          </span>
        </div>
        <div v-if="m.disambiguation.product_id || m.disambiguation.product_name" class="diag-line">
          <span class="diag-line-name">定向</span>
          <span class="diag-line-text"
            >{{ m.disambiguation.product_id }} {{ m.disambiguation.product_name }}</span
          >
        </div>
        <div v-for="c in m.disambiguation.candidates || []" :key="c.product_id" class="diag-line">
          <span class="diag-line-name">{{ c.product_id }}</span>
          <span class="diag-line-text">{{ c.name }}（{{ c.spec }}）</span>
        </div>
      </div>

      <!-- 事件时间线：每个 SSE 事件的相对时刻与间隔，定位卡在哪个环节 -->
      <div v-if="events.length" class="diag-block">
        <div class="diag-caption">事件时间线（相对发送时刻 · 间隔=与上条间隔）</div>
        <div v-for="(e, i) in events" :key="i" class="diag-line">
          <span class="diag-line-t">{{ fmtMs(e.t) }}</span>
          <span class="diag-line-name" :class="`evt-${e.name}`">{{ eventLabel(e.name) }}</span>
          <span v-if="e.dt >= 100" class="diag-line-gap">+{{ fmtMs(e.dt) }}</span>
          <span class="diag-line-text">{{ evtDataText(e) }}</span>
        </div>
      </div>

      <!-- 知识来源（最终排序） -->
      <template v-if="m.sources.length">
        <div class="diag-row">
          <span class="diag-row-label">知识来源（{{ m.sources.length }} 条，最终排序）</span>
        </div>
        <div class="diag-sources">
          <div v-for="(s, i) in m.sources" :key="i" class="diag-source">
            <span class="diag-source-idx">{{ i + 1 }}</span>
            <span class="diag-source-title">{{ s.title || s.snippet || '知识库命中' }}</span>
            <span class="diag-source-doc">{{ s.doc_id || s.source || '' }}</span>
            <span class="diag-source-score">{{ fmtScore(s.score) }}</span>
            <el-button
              v-if="s.text"
              link
              type="primary"
              size="small"
              class="diag-source-toggle"
              @click="toggleSourceDetail(i)"
            >
              {{ m.srcOpen === i ? '收起' : '溯因' }}
            </el-button>
            <div v-if="m.srcOpen === i" class="diag-source-detail">{{ s.text }}</div>
          </div>
        </div>
      </template>
      <div v-else-if="m.status === 'done'" class="diag-row">
        无检索来源（chat/task 意图或缓存命中）
      </div>

      <div class="diag-row">
        <span class="diag-row-label">Trace ID</span>
        <code class="diag-code">{{ m.traceId || '—' }}</code>
        <el-button v-if="m.traceId" link type="primary" size="small" @click="copyText(m.traceId)">
          复制
        </el-button>
      </div>
      <div class="diag-row">
        <span class="diag-row-label">会话 ID</span>
        <code class="diag-code">{{ m.conversationId || '—' }}</code>
        <el-button
          v-if="m.conversationId"
          link
          type="primary"
          size="small"
          @click="copyText(m.conversationId)"
        >
          复制
        </el-button>
      </div>
      <div v-if="m.status === 'done' || m.status === 'error'" class="diag-footer">
        <el-button link type="primary" size="small" @click="copyDiagnosis">
          复制完整诊断 JSON（上报排查）
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * 消息诊断面板（仅 admin/agent 可见）。
 *
 * 从 views/Chat/index.vue 抽出：流式中实时展示阶段/耗时，完成后含来源明细 +
 * 事件时间线 + 阶段耗时 + 完整诊断 JSON 复制，便于定位「卡在哪一环」。
 * 纯格式化逻辑在 utils/chatDiagnostics.js，本组件只负责展示与交互。
 */
import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'
import {
  buildDiagBlob,
  copyText,
  cragLabel,
  displayEvents,
  eventLabel,
  evtDataText,
  fmtMs,
  fmtScore,
  hasBackendTimings,
  intentLabel,
  routeLabel,
  stageBreakdown,
  stageText,
} from '@/utils/chatDiagnostics'

const props = defineProps({
  message: { type: Object, required: true },
  // 当前语气名称（persona id → name），由父组件解析
  personaName: { type: String, default: '' },
  // 本条回答对应的用户提问（诊断 JSON 里带上，便于复现）
  query: { type: String, default: '' },
})

const authStore = useAuthStore()
const m = computed(() => props.message)

// 阶段耗时与事件时间线：模板里 v-if 与 v-for 各用一次，此前每处都重新扫一遍
// 事件日志；流式期间每条 delta 都会重渲染，改成 computed 后每次渲染只算一次
const stages = computed(() => stageBreakdown(m.value))
const events = computed(() => displayEvents(m.value))

// 展开/收起某条检索来源的片段全文（查看溯因）
function toggleSourceDetail(i) {
  m.value.srcOpen = m.value.srcOpen === i ? -1 : i
}

function copyDiagnosis() {
  copyText(
    buildDiagBlob(m.value, {
      query: props.query,
      username: authStore.user?.username || '',
      role: authStore.role || '',
    }),
  )
}
</script>

<style scoped>
/* 诊断信息面板（仅 admin/agent 可见）：意图/路由/耗时/缓存/CRAG/降级 + 时间线 + 来源 */
.diag-section {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--am-line);
}
.diag-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 4px 0;
  border: none;
  background: none;
  cursor: pointer;
  font: inherit;
  color: var(--am-text-2);
  text-align: left;
  flex-wrap: wrap;
}
.diag-header:hover .diag-title {
  color: var(--am-blue-600);
}
.diag-caret {
  color: var(--am-text-3);
  flex-shrink: 0;
  transition: transform 0.18s ease;
}
.diag-header.open .diag-caret {
  transform: rotate(90deg);
}
.diag-title {
  font-weight: 600;
  color: var(--am-text-2);
  font-size: 13px;
  flex-shrink: 0;
}
.diag-chips {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}
.diag-chip {
  display: inline-block;
  padding: 0 8px;
  height: 20px;
  line-height: 20px;
  border-radius: 10px;
  font-size: 12px;
  background: var(--am-blue-50);
  color: var(--am-text-2);
  white-space: nowrap;
}
.diag-chip.chip-intent {
  background: var(--am-blue-600);
  color: #fff;
  font-weight: 600;
}
.diag-chip.chip-cache {
  background: #ecfdf5;
  color: var(--am-green-700);
}
.diag-chip.chip-degraded {
  background: #fef2f2;
  color: var(--am-red-700);
}
.diag-chip.chip-crag {
  background: #f5f3ff;
  color: var(--am-violet-700);
}
.diag-chip.chip-live {
  background: var(--am-blue-50);
  color: var(--am-blue-600);
  animation: diag-pulse 1.4s ease-in-out infinite;
}
@keyframes diag-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.55;
  }
}
.diag-toggle-text {
  font-size: 12px;
  color: var(--am-text-3);
  flex-shrink: 0;
}
.diag-body {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.diag-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  flex-wrap: wrap;
}
.diag-row-label {
  font-weight: 600;
  color: var(--am-text-2);
  font-size: 13px;
}
.diag-code {
  background: var(--am-blue-50);
  border-radius: 3px;
  padding: 1px 6px;
  font-size: 12px;
  font-family: var(--am-font-mono);
  word-break: break-all;
  color: var(--am-text-2);
}
.diag-block {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.diag-caption {
  font-size: 12px;
  font-weight: 600;
  color: var(--am-text-3);
}
.diag-stage-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}
.diag-stage-name {
  width: 76px;
  flex-shrink: 0;
  color: var(--am-text-2);
}
.diag-stage-val {
  font-family: var(--am-font-mono);
  color: var(--am-text);
}
.diag-line {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  line-height: 1.6;
}
.diag-line-idx {
  color: var(--am-blue-600);
  flex-shrink: 0;
  min-width: 14px;
}
.diag-line-t {
  font-family: var(--am-font-mono);
  color: var(--am-text-3);
  flex-shrink: 0;
  min-width: 44px;
}
.diag-line-name {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--am-text-2);
}
.diag-line-name.evt-start {
  color: var(--am-blue-600);
}
.diag-line-name.evt-done {
  color: var(--am-green-700);
}
.diag-line-name.evt-error {
  color: var(--am-red-700);
}
.diag-line-name.evt-tool_call,
.diag-line-name.evt-tool_result {
  color: var(--am-amber-700);
}
.diag-line-gap {
  flex-shrink: 0;
  color: var(--am-red-600);
  font-family: var(--am-font-mono);
  font-size: 11px;
}
.diag-line-text {
  color: var(--am-text-2);
  word-break: break-all;
  min-width: 0;
}
.diag-sources {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.diag-source {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--am-text-2);
  padding: 2px 0;
  flex-wrap: wrap;
}
.diag-source-idx {
  color: var(--am-blue-600);
  flex-shrink: 0;
}
.diag-source-title {
  word-break: break-all;
}
.diag-source-doc {
  color: var(--am-text-3);
  font-size: 12px;
  font-family: var(--am-font-mono);
  word-break: break-all;
}
.diag-source-score {
  color: var(--am-text-2);
  font-size: 12px;
  flex-shrink: 0;
}
.diag-source-toggle {
  flex-shrink: 0;
}
.diag-source-detail {
  width: 100%;
  margin-top: 2px;
  padding: 6px 8px;
  background: var(--am-blue-50);
  border-radius: 4px;
  color: var(--am-text-2);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
.diag-footer {
  display: flex;
  justify-content: flex-end;
}
</style>
