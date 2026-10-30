<template>
  <!-- 人工客服线程（transfer_human 成功后挂接）：工单回复轮询落在此处，
               staff 回复左泡、用户回帖右泡；新消息自动滚底，卸载停止轮询 -->
  <div v-if="ticketId" class="human-thread">
    <div class="human-thread-header">
      <el-tag size="small" type="warning">人工客服</el-tag>
      <span class="human-thread-ticket">{{ ticketId }}</span>
      <span class="muted-text">客服人员已加入处理，可在下方直接回复</span>
    </div>
    <div class="human-thread-msgs">
      <div v-if="!replies.length" class="muted-text human-thread-empty">
        暂无客服回复，客服接入后消息会显示在这里
      </div>
      <div
        v-for="r in replies"
        :key="r.id"
        class="human-thread-row"
        :class="r.sender_role === 'user' ? 'self' : 'staff'"
      >
        <template v-if="r.sender_role !== 'user'">
          <div class="ht-meta">
            <span class="ht-who">人工客服 · {{ r.sender_username }}</span>
            <span class="ht-time">{{ fmtTime(r.created_at) }}</span>
          </div>
          <div class="ht-bubble">{{ r.content }}</div>
        </template>
        <template v-else>
          <div class="ht-meta">
            <span class="ht-time">{{ fmtTime(r.created_at) }}</span>
          </div>
          <div class="ht-bubble self">{{ r.content }}</div>
        </template>
      </div>
    </div>
    <div class="human-thread-input">
      <el-input
        v-model="draftModel"
        placeholder="回复人工客服…"
        maxlength="2000"
        @keydown.enter="sendReply"
      />
      <el-button
        type="primary"
        size="small"
        :loading="sending"
        :disabled="!draft.trim()"
        @click="sendReply"
      >
        发送
      </el-button>
    </div>
  </div>
</template>

<script setup>
/**
 * 人工客服线程面板（transfer_human 成功后出现在消息流末尾）。
 *
 * 从 views/Chat/index.vue 抽出：工单回复列表（staff 左泡 / 用户右泡）+ 回帖输入。
 * 数据与轮询在 composables/useHumanThread.js，本组件只负责展示、滚底与回帖输入。
 * 新消息自动滚底在此实现：监听 replies 长度变化（轮询合并与本地回帖都会增长）。
 */
import { ref, computed, watch, nextTick } from 'vue'
import { fmtTime } from '@/utils/datetime'

const props = defineProps({
  ticketId: { type: String, default: '' },
  replies: { type: Array, default: () => [] },
  sending: { type: Boolean, default: false },
  draft: { type: String, default: '' },
})

const emit = defineEmits(['update:draft', 'send'])

const threadListRef = ref(null)

// 回帖草稿：受控组件，写回交页面（threadDraft 仍由 useHumanThread 持有）
const draftModel = computed({
  get: () => props.draft,
  set: (v) => emit('update:draft', v),
})

function scrollToBottom() {
  nextTick(() => {
    const el = threadListRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

watch(() => props.replies.length, scrollToBottom)

function sendReply() {
  emit('send')
}
</script>

<style scoped>
.muted-text {
  color: var(--am-text-3);
  font-size: 13px;
}

/* 人工客服线程（transfer_human 成功后出现在消息流末尾）：琥珀色卡片区分 AI 气泡 */
.human-thread {
  margin: 6px 0 12px;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--am-amber-700) 30%, var(--am-line));
  border-radius: 10px;
  background: #fff;
}
.human-thread-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--am-line);
}
.human-thread-ticket {
  font-family: var(--am-font-mono);
  font-size: 12px;
  color: var(--am-text-2);
}
.human-thread-msgs {
  max-height: 240px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 2px 4px;
}
.human-thread-empty {
  text-align: center;
  padding: 8px 0;
  font-size: 13px;
}
.human-thread-row {
  display: flex;
  flex-direction: column;
  max-width: 78%;
}
.human-thread-row.staff {
  align-self: flex-start;
}
.human-thread-row.self {
  align-self: flex-end;
  align-items: flex-end;
}
.ht-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
  font-size: 12px;
}
.ht-who {
  font-weight: 600;
  color: var(--am-amber-700);
}
.ht-time {
  color: var(--am-text-3);
}
.ht-bubble {
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
  white-space: pre-wrap;
}
.human-thread-row.staff .ht-bubble {
  background: var(--am-paper);
  border: 1px solid var(--am-line);
  border-top-left-radius: 3px;
  color: var(--am-text);
}
.ht-bubble.self {
  background: linear-gradient(135deg, var(--am-blue-600), var(--am-blue-500));
  color: #fff;
  border-top-right-radius: 3px;
}
.human-thread-input {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--am-line);
}

@media (max-width: 767px) {
  .human-thread-input {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
