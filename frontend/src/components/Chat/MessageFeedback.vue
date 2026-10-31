<template>
  <div class="feedback-row">
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
          @click="emit('submit')"
        >
          提交
        </button>
      </div>
    </template>
    <span v-else class="muted-text">已提交反馈，感谢您的反馈！</span>
  </div>
</template>

<script setup>
/**
 * 单条回答的满意度反馈条（Bad Case 收集入口）：口袋赞踩 + 内联评论 + 提交。
 *
 * 从 components/Chat/MessageBubble.vue 抽出（原为气泡内 65 行模板 + 100 行样式）。
 * 评分/评论/提交态都挂在消息对象上，本组件直接读写该对象（与页面共享同一份状态），
 * 提交动作抛回页面（需要相邻用户消息与接口调用，不属于展示层）。
 */
import { computed } from 'vue'

const props = defineProps({
  message: { type: Object, required: true },
})

const emit = defineEmits(['submit'])

// 用 computed 取消息对象：模板里对评分的写入落在原响应式对象上，不产生本地副本
const m = computed(() => props.message)
</script>

<style scoped>
.muted-text {
  color: var(--am-text-3);
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
