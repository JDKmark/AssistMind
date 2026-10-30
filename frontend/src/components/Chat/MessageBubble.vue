<template>
  <div class="chat-row" :class="m.role">
    <div class="bubble" :class="m.role">
      <template v-if="m.role === 'user'">
        <div class="bubble-text">{{ m.content }}</div>
      </template>
      <template v-else>
        <!-- 流式状态 -->
        <div v-if="m.status !== 'done' && m.status !== 'error'" class="status-line">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>{{ stageText(m) }}</span>
        </div>

        <!-- 查询改写（内部机制：仅 staff 可见；普通用户只感知"正在检索"） -->
        <div v-if="isStaff && m.rewrites && m.rewrites.length" class="rewrite-line">
          <el-tag size="small" type="info" effect="plain">查询已改写</el-tag>
          <span class="muted-text">已根据您的描述扩展检索</span>
        </div>

        <ToolCallCard v-if="m.toolCalls.length" :tool-calls="m.toolCalls" :is-staff="isStaff" />

        <!-- 工单提示 -->
        <el-alert v-if="m.ticketId" type="success" :closable="false" show-icon class="ticket-alert">
          <template #title>
            已创建工单
            {{ m.ticketId }}，前往
            <router-link to="/tickets" class="ticket-link">工单列表</router-link>
          </template>
        </el-alert>

        <!-- 回答 -->
        <div v-if="m.content" class="bubble-text md-body" v-html="renderMd(m.content)" />
        <div v-else-if="m.status === 'done'" class="bubble-text muted-text">
          （暂无回答，可尝试转人工客服）
        </div>

        <!-- 产品消歧候选卡片（phase15，clarify 反问回合）：点击以该商品名
                   走既有发送链路发出，下一轮经别名召回/信号词自动定向 -->
        <div
          v-if="
            m.disambiguation &&
            m.disambiguation.status === 'clarify' &&
            (m.disambiguation.candidates || []).length
          "
          class="disambig-cards"
        >
          <el-button
            v-for="c in m.disambiguation.candidates"
            :key="c.product_id"
            class="disambig-card"
            @click="sendDisambigChoice(c)"
          >
            <span class="disambig-card-body">
              <span class="disambig-card-name">{{ c.name }}</span>
              <span class="disambig-card-spec">{{ c.spec }}</span>
              <span class="disambig-card-hint">点击咨询这款 →</span>
            </span>
          </el-button>
        </div>

        <!-- 诊断信息（仅 admin/agent 可见）：流式中实时展示阶段/耗时，完成后含
                   来源明细 + 事件时间线 + 阶段耗时 + 完整诊断 JSON 复制（便于定位问题） -->
        <ChatDiagnostics
          v-if="isStaff && (m.started || m.status === 'done')"
          :message="m"
          :persona-name="personaName"
          :query="query"
        />

        <!-- 反馈（Bad Case 收集入口）：口袋按钮 + 内联评论行 -->
        <div v-if="m.status === 'done' && !m.isNotice" class="feedback-row">
          <template v-if="!m.feedbackSubmitted">
            <div class="fb-group">
              <button
                class="fb-btn"
                :class="{ 'is-active': m.feedbackScore === 5 }"
                :title="m.feedbackScore === 5 ? '已选' : '有帮助'"
                @click="m.feedbackScore = 5"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                >
                  <path
                    d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"
                  />
                </svg>
              </button>
              <button
                class="fb-btn fb-btn-down"
                :class="{ 'is-active': m.feedbackScore === 1 }"
                :title="m.feedbackScore === 1 ? '已选' : '没帮助'"
                @click="m.feedbackScore = 1"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                >
                  <path
                    d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7 0h3a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-3"
                  />
                </svg>
              </button>
              <textarea
                v-model="m.feedbackComment"
                class="fb-comment"
                rows="1"
                placeholder="补充说明（选填）"
                maxlength="1000"
              />
              <button
                class="fb-submit"
                :disabled="m.feedbackSubmitting"
                :class="{ 'is-loading': m.feedbackSubmitting }"
                @click="submitFeedback"
              >
                提交
              </button>
            </div>
          </template>
          <span v-else class="muted-text">已提交反馈，感谢您的反馈！</span>
        </div>

        <!-- 错误提示 -->
        <el-alert
          v-if="m.status === 'error'"
          type="error"
          :closable="false"
          show-icon
          class="error-alert"
          :title="m.errorMsg || '服务异常，请重试'"
        />
      </template>
    </div>
  </div>
</template>

<script setup>
/**
 * 单条消息气泡（用户 / 客服）。
 *
 * 从 views/Chat/index.vue 抽出：流式状态、查询改写、工具调用、工单提示、回答、
 * 消歧候选卡片、诊断面板、反馈条、错误提示。所有业务状态都挂在消息对象上，
 * 本组件只按角色与状态渲染，动作通过事件交回页面。
 */
import { computed } from 'vue'
import ChatDiagnostics from '@/components/Chat/ChatDiagnostics.vue'
import ToolCallCard from '@/components/Chat/ToolCallCard.vue'
import { stageText } from '@/utils/chatDiagnostics'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps({
  message: { type: Object, required: true },
  // 内部机制（改写标签 / 工具名与参数 / 诊断面板）仅 staff 可见
  isStaff: { type: Boolean, default: false },
  // 当前语气名称（persona id → name），由页面解析后下传
  personaName: { type: String, default: '' },
  // 本条回答对应的用户提问（诊断 JSON 里带上，便于复现）
  query: { type: String, default: '' },
})

const emit = defineEmits(['disambig-choice', 'feedback-submit'])

// 用 computed 取消息对象：模板里对反馈字段的写入落在原响应式对象上，
// 与消息列表共享同一份状态（不是组件内部副本）
const m = computed(() => props.message)

function renderMd(text) {
  return renderMarkdown(text)
}

function sendDisambigChoice(candidate) {
  emit('disambig-choice', candidate)
}

function submitFeedback() {
  emit('feedback-submit', props.message)
}
</script>

<style scoped>
.muted-text {
  color: var(--am-text-3);
  font-size: 13px;
}

.chat-row {
  display: flex;
  margin-bottom: 12px;
}
.chat-row.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.65;
  word-break: break-word;
}
.bubble.user {
  background: linear-gradient(135deg, var(--am-blue-600), var(--am-blue-500));
  color: #fff;
  border-top-right-radius: 4px;
  box-shadow: 0 2px 8px rgba(35, 82, 197, 0.22);
}
.bubble.assistant {
  background: var(--am-card);
  color: var(--am-text);
  border: 1px solid var(--am-line);
  box-shadow: 0 1px 3px rgba(14, 33, 64, 0.05);
  border-top-left-radius: 4px;
  font-size: 14.5px;
  line-height: 1.7;
}
.bubble-text {
  white-space: pre-wrap;
}
.bubble.user .bubble-text {
  white-space: normal;
}

/* 状态行 */
.status-line {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--am-text-3);
  font-size: 13px;
}
.rewrite-line {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 0;
  font-size: 13px;
}

/* 反馈（Bad Case 收集入口）：评分条 = 口袋赞踩 + 内联评论 + 提交 */
.feedback-row {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--am-line);
}
.fb-group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border: 1px solid var(--am-line);
  border-radius: 999px;
  background: #fff;
  max-width: 100%;
}
.fb-btn {
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--am-text-3);
  cursor: pointer;
  transition:
    background 0.16s ease,
    color 0.16s ease,
    transform 0.16s ease;
}
.fb-btn:hover {
  background: rgba(94, 141, 234, 0.12);
  color: var(--am-blue-600);
}
.fb-btn.is-active {
  background: var(--am-blue-600);
  color: #fff;
}
.fb-btn.is-active.fb-btn-down {
  background: var(--el-color-danger);
}
.fb-comment {
  flex: 1;
  min-width: 120px;
  width: 200px;
  border: none;
  outline: none;
  background: transparent;
  font-size: 13px;
  font-family: inherit;
  color: var(--am-text);
  resize: none;
  padding: 2px 4px;
}
.fb-comment::placeholder {
  color: var(--am-text-3);
}
.fb-submit {
  flex-shrink: 0;
  height: 26px;
  padding: 0 12px;
  border: none;
  border-radius: 13px;
  background: var(--am-blue-50);
  color: var(--am-blue-600);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition:
    background 0.16s ease,
    color 0.16s ease;
}
.fb-submit:hover {
  background: var(--am-blue-600);
  color: #fff;
}
.fb-submit.is-loading {
  opacity: 0.6;
  pointer-events: none;
}

/* 工单 / 诊断提示 */
.ticket-alert {
  margin: 8px 0 4px;
}
.ticket-link {
  color: var(--am-blue-600);
  text-decoration: none;
}
.error-alert {
  margin-top: 6px;
}

/* markdown 回答 */
.md-body :deep(p) {
  margin: 4px 0;
}
.md-body :deep(pre) {
  background: var(--am-blue-50);
  border-radius: 4px;
  padding: 8px;
  overflow-x: auto;
  font-size: 12px;
}
.md-body :deep(code) {
  background: var(--am-blue-50);
  border-radius: 3px;
  padding: 1px 4px;
  font-size: 12px;
}
.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
/* LLM 输出的标题/表格/引用块（P2-1）：此前未样式化会裸奔 */
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4) {
  margin: 0.8em 0 0.4em;
  font-weight: 600;
  line-height: 1.4;
  color: var(--am-text);
}
.md-body :deep(h1) {
  font-size: 1.15em;
}
.md-body :deep(h2) {
  font-size: 1.08em;
}
.md-body :deep(h3),
.md-body :deep(h4) {
  font-size: 1em;
}
.md-body :deep(table) {
  border-collapse: collapse;
  margin: 0.6em 0;
  font-size: 0.92em;
  display: block; /* 窄气泡内可横滑，不撑破气泡宽度 */
  overflow-x: auto;
}
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid var(--am-line);
  padding: 5px 10px;
  text-align: left;
  white-space: nowrap;
}
.md-body :deep(th) {
  background: var(--am-blue-50);
  font-weight: 600;
}
.md-body :deep(blockquote) {
  margin: 0.6em 0;
  padding: 4px 12px;
  border-left: 3px solid var(--am-blue-200);
  color: var(--am-text-2);
}

/* 产品消歧候选卡片（phase15，clarify 反问消息下方） */
.disambig-cards {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.disambig-card.el-button {
  height: auto;
  padding: 8px 12px;
  border: 1px solid var(--am-line);
  background: var(--am-paper);
  text-align: left;
}
.disambig-card.el-button:hover {
  border-color: var(--am-blue-600);
  background: var(--am-blue-50);
}
.disambig-card-body {
  display: block;
}
.disambig-card-name {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--am-text);
}
.disambig-card-spec {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: var(--am-text-3);
}
.disambig-card-hint {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: var(--am-blue-600);
}

@media (max-width: 767px) {
  .fb-btn {
    width: 40px;
    height: 40px;
  }
  .fb-group {
    gap: 2px;
    padding: 3px 6px;
  }
  .fb-submit {
    height: 34px;
  }
}
</style>
