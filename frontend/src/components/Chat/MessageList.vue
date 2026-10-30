<template>
  <div v-if="messages.length === 0" class="chat-empty">
    <el-empty description="您好，我是 AssistMind 智能客服，请问有什么可以帮您？" :image-size="80" />
  </div>

  <MessageBubble
    v-for="(m, i) in messages"
    :key="m.id"
    :message="m"
    :is-staff="isStaff"
    :persona-name="personaName"
    :query="i > 0 ? messages[i - 1].content : ''"
    @disambig-choice="emit('disambig-choice', $event)"
    @feedback-submit="emit('feedback-submit', $event)"
  />
</template>

<script setup>
/**
 * 消息流（空态 + 气泡列表）。
 *
 * 从 views/Chat/index.vue 抽出。滚动视口（.chat-messages）与人工客服线程仍留在页面：
 * 前者承载「贴底才跟随」的滚动语义，后者是独立面板，二者都不属于消息流本身。
 * 诊断所需的「本条回答对应的提问」在这里按索引取上一条，避免把整张列表传给气泡。
 */
import MessageBubble from '@/components/Chat/MessageBubble.vue'

defineProps({
  messages: { type: Array, required: true },
  isStaff: { type: Boolean, default: false },
  personaName: { type: String, default: '' },
})

const emit = defineEmits(['disambig-choice', 'feedback-submit'])
</script>

<style scoped>
.chat-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 240px;
}
</style>
