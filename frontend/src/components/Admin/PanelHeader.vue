<template>
  <div class="card-header">
    <span class="card-title">{{ title }}</span>
    <slot />
  </div>
</template>

<script setup>
/**
 * 面板头部（标题 + 右侧操作区）。
 *
 * 系统状态 / 数据概览 / 工单管理 / 反馈与追溯 四个面板的 header 结构完全一致，
 * 此前每个组件各抄一份标记与 `.card-header/.card-title` 样式；抽成组件后
 * DOM 不变（仍是 div.card-header > span.card-title + 插槽内容），
 * 结构与样式都只有一处定义。
 * 用户管理 / 退款处理 / 操作审计 三张表只有裸标题、没有右侧操作区，
 * 用本组件会多包一层 div，故保持原样。
 */
defineProps({
  title: { type: String, required: true },
})
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title {
  font-weight: 600;
}
</style>
