<template>
  <!-- 工单详情（管理员查看）：右侧抽屉 -->
  <el-drawer v-model="visibleModel" title="工单详情" :size="isMobile ? '100%' : '560px'">
    <div v-loading="loading" class="detail-body">
      <el-descriptions v-if="detail" :column="1" border>
        <el-descriptions-item label="工单号">
          <span class="am-mono ticket-id-text">{{ detail.id }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="客户">
          {{ detail.user_id }}
        </el-descriptions-item>
        <el-descriptions-item label="标题">{{ detail.title }}</el-descriptions-item>
        <el-descriptions-item label="描述">
          {{ detail.description }}
        </el-descriptions-item>
        <el-descriptions-item label="优先级">
          <el-tag
            :type="priorityTagType(detail.priority)"
            size="small"
            effect="plain"
            class="am-tag-pill"
          >
            {{ priorityLabel(detail.priority) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag
            :type="ticketStatusTagType(detail.status)"
            size="small"
            effect="plain"
            class="am-tag-pill"
          >
            {{ ticketStatusLabel(detail.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="创建时间">
          {{ fmtTime(detail.created_at) }}
        </el-descriptions-item>
        <el-descriptions-item label="更新时间">
          {{ fmtTime(detail.updated_at) }}
        </el-descriptions-item>
      </el-descriptions>

      <!-- 人工介入沟通线程：回复 open 工单自动转为处理中 -->
      <div class="reply-section">
        <div class="reply-title">沟通记录</div>
        <div v-if="replies.length" class="reply-list">
          <div v-for="r in replies" :key="r.id" class="reply-item">
            <div class="reply-meta">
              <el-tag
                size="small"
                :type="r.sender_role === 'user' ? 'info' : 'warning'"
                effect="plain"
              >
                {{
                  r.sender_role === 'admin'
                    ? '管理员 '
                    : r.sender_role === 'agent'
                      ? '客服 '
                      : '用户 '
                }}{{ r.sender_username }}
              </el-tag>
              <span class="reply-time">{{ fmtTime(r.created_at) }}</span>
            </div>
            <div class="reply-content">{{ r.content }}</div>
          </div>
        </div>
        <div v-else class="muted-text reply-empty">暂无沟通记录，回复后用户可见</div>

        <div class="reply-input-row">
          <el-input
            v-model="draftModel"
            type="textarea"
            :rows="2"
            maxlength="2000"
            placeholder="以人工身份回复用户（open 工单将自动转为处理中）"
          />
          <el-button
            type="primary"
            :loading="submitting"
            :disabled="!replyDraft.trim()"
            @click="emit('send-reply')"
          >
            发送回复
          </el-button>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
/**
 * 工单详情抽屉（管理员查看 + 人工回复）。
 *
 * 从 views/Admin/index.vue 抽出。详情与回复数据由 composables/useAdminTickets.js 持有，
 * 本组件只负责渲染与回帖输入；回复成功后 open 工单转「处理中」由 composable 处理。
 */
import { computed } from 'vue'
import { fmtTime } from '@/utils/datetime'
import {
  ticketStatusLabel,
  ticketStatusTagType,
  priorityLabel,
  priorityTagType,
} from '@/utils/labels'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  isMobile: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  detail: { type: Object, default: null },
  replies: { type: Array, default: () => [] },
  replyDraft: { type: String, default: '' },
  submitting: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'update:replyDraft', 'send-reply'])

const visibleModel = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const draftModel = computed({
  get: () => props.replyDraft,
  set: (v) => emit('update:replyDraft', v),
})
</script>

<style scoped>
.muted-text {
  color: var(--am-text-3);
  font-size: 13px;
}
.detail-body {
  min-height: 80px;
}
.ticket-id-text {
  font-family: ui-monospace, monospace;
  font-size: 13px;
  word-break: break-all;
}
.reply-section {
  margin-top: 16px;
}
.reply-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--am-text-2);
  margin-bottom: 8px;
}
.reply-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 260px;
  overflow-y: auto;
  margin-bottom: 12px;
}
.reply-item {
  padding: 8px 10px;
  background: var(--am-paper);
  border-radius: 6px;
}
.reply-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.reply-time {
  font-size: 12px;
  color: var(--am-text-3);
}
.reply-content {
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.reply-empty {
  margin-bottom: 12px;
}
.reply-input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

@media (max-width: 767px) {
  .reply-input-row {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
