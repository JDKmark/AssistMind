<template>
  <div class="feedback-bar">
    <el-form :model="form" label-position="top">
      <el-form-item label="满意度评分">
        <el-rate v-model="form.score" />
      </el-form-item>
      <el-form-item label="反馈内容">
        <el-input
          v-model="form.comment"
          type="textarea"
          :rows="3"
          placeholder="请输入您的反馈（可选）"
          maxlength="1000"
          show-word-limit
        />
      </el-form-item>
      <el-form-item>
        <el-button
          type="primary"
          :loading="submitting"
          :disabled="form.score === 0"
          @click="handleSubmit"
        >
          提交反馈
        </el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { submitFeedback } from '@/api/feedback'

const props = defineProps({
  ticketId: {
    type: String,
    default: '',
  },
})

const submitting = ref(false)
const form = reactive({
  score: 0,
  comment: '',
})

async function handleSubmit() {
  if (form.score === 0) {
    ElMessage.warning('请先选择评分')
    return
  }
  submitting.value = true
  try {
    await submitFeedback({
      score: form.score,
      comment: form.comment,
      ticket_id: props.ticketId,
    })
    ElMessage.success('感谢您的反馈！')
    form.score = 0
    form.comment = ''
  } catch (e) {
    // 错误已由 request 拦截器统一提示
  } finally {
    submitting.value = false
  }
}

defineExpose({ form, handleSubmit })
</script>

<style scoped>
.feedback-bar {
  padding: 12px;
}
</style>
