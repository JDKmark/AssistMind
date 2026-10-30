<template>
  <div class="admin-page">
    <!-- 后端不可用降级横幅：任一请求 5xx/网络失败时展示，恢复后自动消失 -->
    <el-alert
      v-if="backendStatus.down"
      type="error"
      :closable="false"
      show-icon
      class="degraded-banner"
    >
      <template #title>
        后端服务暂不可用，当前页面数据可能不完整或为空。
        <el-button size="small" type="primary" link @click="retryAll">重试</el-button>
      </template>
    </el-alert>

    <el-row :gutter="16">
      <!-- 系统状态 -->
      <el-col :xs="24" :sm="12">
        <HealthPanel
          :dep-rows="depRows"
          :overall-label="overallLabel"
          :overall-tag-type="overallTagType"
        />
      </el-col>

      <!-- 数据概览 -->
      <el-col :xs="24" :sm="12">
        <OverviewCards
          :kb-total="kbTotal"
          :kb-chunks="kbChunks"
          :ticket-total="ticketTotal"
          :status-dist="statusDist"
          :kb-error="kbError"
          :data-loading="dataLoading"
        />
      </el-col>
    </el-row>

    <TicketPanel
      v-model:status-filter="ticketStatusFilter"
      v-model:priority-filter="ticketPriorityFilter"
      v-model:customer-filter="ticketCustomerFilter"
      :tickets="tickets"
      @search="searchTickets"
      @open-detail="openTicketDetail"
    />

    <TicketDetailDrawer
      v-model="ticketDetailVisible"
      :is-mobile="isMobile"
      :loading="ticketDetailLoading"
      :detail="currentTicketDetail"
      :replies="ticketReplies"
      :reply-draft="ticketReplyDraft"
      :submitting="replySubmitting"
      @update:reply-draft="ticketReplyDraft = $event"
      @send-reply="sendTicketReply"
    />

    <UserTable :users="users" @change-role="changeUserRole" @toggle="toggleUser" />

    <RefundPanel :refunds="refunds" @change-status="changeRefundStatus" />

    <AuditPanel :audit-logs="auditLogs" />

    <AdminOrders ref="ordersRef" />

    <FeedbackPanel
      v-model:score-filter="fbScoreFilter"
      v-model:exported-filter="fbExportedFilter"
      :feedbacks="feedbacks"
      :loading="fbLoading"
      :error="fbError"
      :eval-running="evalRunning"
      :eval-status-text="evalStatusText"
      @refresh="loadFeedback"
      @run-eval="runEvaluation"
      @open-trace="openTrace"
    />

    <TraceDrawer
      v-model="traceDrawer"
      :is-mobile="isMobile"
      :trace="currentTrace"
      :crag-label="cragLabel"
      :crag-tag-type="cragTagType"
      :langfuse-host="langfuseHost"
      :langfuse-url="langfuseUrl"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { backendStatus } from '@/api/request'
import { useBreakpoint } from '@/utils/useBreakpoint'
import AdminOrders from '@/components/Admin/AdminOrders.vue'
import AuditPanel from '@/components/Admin/AuditPanel.vue'
import FeedbackPanel from '@/components/Admin/FeedbackPanel.vue'
import HealthPanel from '@/components/Admin/HealthPanel.vue'
import OverviewCards from '@/components/Admin/OverviewCards.vue'
import RefundPanel from '@/components/Admin/RefundPanel.vue'
import TicketDetailDrawer from '@/components/Admin/TicketDetailDrawer.vue'
import TicketPanel from '@/components/Admin/TicketPanel.vue'
import TraceDrawer from '@/components/Admin/TraceDrawer.vue'
import UserTable from '@/components/Admin/UserTable.vue'
import { useAdminHealth } from '@/composables/useAdminHealth'
import { useAdminTickets } from '@/composables/useAdminTickets'
import { useAdminOverview } from '@/composables/useAdminOverview'
import { useAdminUsers } from '@/composables/useAdminUsers'
import { useAdminRefunds } from '@/composables/useAdminRefunds'
import { useAdminFeedback } from '@/composables/useAdminFeedback'
import { useAdminEval } from '@/composables/useAdminEval'

// 商城订单面板（子组件）：顶部「全部重试」需要触发它重新加载
const ordersRef = ref(null)

// 移动端抽屉全宽 / 表格横滚（P0-2 / P0-3）
const { isMobile } = useBreakpoint()

// ---------- 组合式模块（顺序即依赖顺序：工单快照 → 概览 → 用户 → 退款） ----------
const { depRows, overallLabel, overallTagType, loadHealth } = useAdminHealth()

const {
  tickets,
  ticketTotal,
  ticketStatusFilter,
  ticketPriorityFilter,
  ticketCustomerFilter,
  statusDist,
  applyTickets,
  searchTickets,
  ticketDetailVisible,
  ticketDetailLoading,
  currentTicketDetail,
  ticketReplies,
  ticketReplyDraft,
  replySubmitting,
  openTicketDetail,
  sendTicketReply,
} = useAdminTickets()

const { adminOverview, kbTotal, kbChunks, kbError, dataLoading, loadOverview } = useAdminOverview({
  applyTickets,
})

const { users, auditLogs, loadAdminData, changeUserRole, toggleUser } = useAdminUsers({
  adminOverview,
})

const { refunds, loadRefunds, changeRefundStatus } = useAdminRefunds({
  reloadAdminData: () => loadAdminData(),
})

const {
  feedbacks,
  fbLoading,
  fbError,
  fbScoreFilter,
  fbExportedFilter,
  traceDrawer,
  currentTrace,
  langfuseHost,
  cragLabel,
  cragTagType,
  langfuseUrl,
  loadFeedback,
  openTrace,
} = useAdminFeedback()

const { evalRunning, evalStatusText, runEvaluation, dispose: disposeEval } = useAdminEval()

// 后端不可用横幅的「重试」：把页面所有数据源并行拉一遍，失败互不影响
async function retryAll() {
  await Promise.allSettled([
    loadHealth(),
    loadOverview(),
    loadAdminData(),
    loadRefunds(),
    ordersRef.value?.load(),
    loadFeedback(),
  ])
}

onMounted(() => {
  loadHealth()
  loadOverview()
  loadAdminData()
  loadRefunds()
  // 商城订单由 <AdminOrders> 自行挂载加载
  loadFeedback()
})

// 卸载：停评估轮询并唤醒在途等待（否则还要挂满一个轮询周期）
onUnmounted(() => {
  disposeEval()
})
</script>

<style scoped>
.admin-page {
  padding: 20px;
}
.degraded-banner {
  margin-bottom: 16px;
}
.panel-card {
  margin-bottom: 16px;
}
/* 反馈卡与上方面板留出更大间隔（根节点继承父作用域，故留在页面） */
.feedback-card {
  margin-top: 16px;
}
</style>
