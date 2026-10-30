<template>
  <el-button size="small" :loading="running" @click="emit('run')">运行评估</el-button>
  <span v-if="statusText" class="muted-text">{{ statusText }}</span>
</template>

<script setup>
/**
 * 评估入口控件（运行按钮 + 运行态文案）。
 *
 * 从 views/Admin/index.vue 的反馈卡 header 抽出。原位置是「按钮 + 状态文本」
 * 两个并列的 flex 子项，故本组件保持双根节点（不额外包一层，避免改变
 * .feedback-filters 的 gap 间距）。轮询逻辑在 composables/useAdminEval.js。
 */
defineProps({
  running: { type: Boolean, default: false },
  statusText: { type: String, default: '' },
})

const emit = defineEmits(['run'])
</script>

<style scoped>
.muted-text {
  color: var(--am-text-3);
  font-size: 13px;
}
</style>
