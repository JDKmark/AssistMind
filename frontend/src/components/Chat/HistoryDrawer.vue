<template>
  <!-- 历史会话抽屉：列出本人会话（updated_at 倒序），支持查看消息与继续对话；
         移动端（<768px）全屏打开避免 380px 固定宽度溢出小屏（P0-2） -->
  <el-drawer
    v-model="visibleModel"
    title="历史会话"
    :size="isMobile ? '100%' : '380px'"
    class="history-drawer"
  >
    <div class="history-toolbar">
      <el-button size="small" type="primary" plain @click="emit('new-conversation')">
        新对话
      </el-button>
    </div>

    <div v-if="loading" class="history-loading muted-text">加载中…</div>
    <el-empty v-else-if="conversations.length === 0" description="暂无历史会话" :image-size="60" />
    <template v-else>
      <div v-for="c in conversations" :key="c.id" class="conversation-item">
        <div class="conversation-info">
          <div class="conversation-title" :title="c.title">{{ c.title }}</div>
          <div class="conversation-meta muted-text">
            <span>{{ fmtTime(c.updated_at) }}</span>
            <span>{{ c.message_count }} 条消息</span>
          </div>
        </div>
        <div class="conversation-actions">
          <el-button size="small" link type="primary" @click="emit('view', c)">查看</el-button>
          <el-button size="small" link type="success" @click="emit('continue', c)">
            继续对话
          </el-button>
        </div>
      </div>
    </template>

    <!-- 查看模式：抽屉内下方展开该会话消息（正序，仅展示角色名与内容） -->
    <div v-if="viewedMessages.length" class="viewed-section">
      <div class="viewed-header muted-text">会话消息（按时间正序）</div>
      <div v-for="(m, i) in viewedMessages" :key="i" class="viewed-msg">
        <span class="viewed-role">{{ m.role === 'user' ? '用户' : '客服' }}</span>
        <span class="viewed-content">{{ m.content }}</span>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
/**
 * 历史会话抽屉：列出本人会话（updated_at 倒序），支持查看消息与继续对话。
 *
 * 从 views/Chat/index.vue 抽出。数据拉取与「继续对话 / 新对话」的副作用
 * （重建 history、清空消息区）都在 composables/useConversationHistory.js，
 * 本组件只负责展示并把三个动作抛回页面。
 * 移动端（<768px）全屏打开，避免 380px 固定宽度溢出小屏（P0-2）。
 */
import { computed } from 'vue'
import { fmtTime } from '@/utils/datetime'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  isMobile: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  conversations: { type: Array, default: () => [] },
  viewedMessages: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:modelValue', 'new-conversation', 'view', 'continue'])

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

/* 历史会话抽屉 */
.history-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}
.history-loading {
  padding: 24px 0;
  text-align: center;
}
.conversation-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 4px;
  border-bottom: 1px solid var(--am-line);
}
.conversation-info {
  flex: 1;
  min-width: 0;
}
.conversation-title {
  font-size: 14px;
  color: var(--am-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.conversation-meta {
  display: flex;
  gap: 8px;
  margin-top: 2px;
  font-size: 12px;
}
.conversation-actions {
  flex-shrink: 0;
  display: flex;
  gap: 2px;
}
.viewed-section {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--am-line);
}
.viewed-header {
  margin-bottom: 8px;
  font-size: 13px;
}
.viewed-msg {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
  padding: 6px 8px;
  border-radius: 6px;
  background: var(--am-blue-50);
  font-size: 13px;
  line-height: 1.6;
}
.viewed-role {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--am-blue-600);
}
.viewed-content {
  word-break: break-word;
  white-space: pre-wrap;
}
</style>
