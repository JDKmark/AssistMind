<template>
  <div class="chat-page">
    <el-card shadow="never" class="panel-card chat-panel">
      <template #header>
        <div class="card-header">
          <span class="card-title">智能客服</span>
          <div class="header-right">
            <span class="muted-text">可咨询商品、订单、物流与售后问题</span>
            <!-- 转人工：常驻入口（成熟 SaaS 客服台模式），点击直接发起转人工会话 -->
            <el-button size="small" plain class="human-btn" @click="handleTransferHuman">
              <svg
                viewBox="0 0 24 24"
                width="14"
                height="14"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path d="M4 13c0-4.4 3.6-8 8-8s8 3.6 8 8" />
                <rect x="2.5" y="13" width="4" height="6" rx="1.5" />
                <rect x="17.5" y="13" width="4" height="6" rx="1.5" />
                <path d="M20 17v1a2 2 0 0 1-2 2h-4" />
              </svg>
              <span>转人工</span>
            </el-button>
            <!-- 历史会话入口（抽屉：查看/继续对话/新对话） -->
            <el-button size="small" plain @click="openHistoryDrawer">历史会话</el-button>
            <!-- 角色语气选择（人格库外置后端，热加载） -->
            <el-select
              v-model="personaStore.personaId"
              class="persona-select"
              size="small"
              placeholder="默认语气"
              clearable
              style="width: 120px"
            >
              <el-option
                v-for="p in personaStore.personas"
                :key="p.id"
                :label="p.name"
                :value="p.id"
              />
            </el-select>
          </div>
        </div>
      </template>

      <!-- 消息列表（onListScroll：贴底才跟随流式滚动，P1-4） -->
      <div ref="listRef" class="chat-messages" @scroll="onListScroll">
        <div class="chat-thread">
          <MessageList
            :messages="messages"
            :is-staff="isStaff"
            :persona-name="personaName"
            @disambig-choice="sendDisambigChoice"
            @feedback-submit="submitMessageFeedback"
          />

          <HumanThreadPanel
            v-if="humanThreadTicketId"
            :ticket-id="humanThreadTicketId"
            :replies="humanThread.replies"
            :sending="threadSending"
            :draft="threadDraft"
            @update:draft="threadDraft = $event"
            @send="sendThreadReply"
          />
        </div>
      </div>

      <!-- 快捷提问（建议卡片网格：白卡 + 类别色点，点击填入输入框） -->
      <div class="quick-row">
        <span class="quick-caption">快捷提问</span>
        <el-button
          v-for="qq in quickQuestions"
          :key="qq.text"
          link
          type="primary"
          class="quick-btn"
          :class="`tone-${qq.tone}`"
          @click="inputText = qq.text"
        >
          <span class="quick-dot" />
          <span class="quick-label">{{ qq.label }}</span>
          <span class="quick-example">{{ qq.text }}</span>
        </el-button>
      </div>

      <!-- 输入区：Trae 风格 AI 对话输入框（自动增高 / 语音输入 / 发送状态机） -->
      <AiInputBox
        v-model="inputText"
        :speak-enabled="speakEnabled"
        :streaming="streaming"
        @send="onSend"
        @stop="stopStream"
        @speak-toggle="speakEnabled = !speakEnabled"
      />
    </el-card>

    <HistoryDrawer
      v-model="historyDrawerVisible"
      :is-mobile="isMobile"
      :loading="historyLoading"
      :conversations="conversations"
      :viewed-messages="viewedMessages"
      @new-conversation="startNewConversation"
      @view="viewConversation"
      @continue="continueConversation"
    />
  </div>
</template>

<script setup>
// 显式命名：MainLayout 的 <keep-alive include="Chat"> 按组件名缓存实例，
// 避免从聊天页跳转工单/订单等页面后对话（组件内存 ref）随卸载全部丢失
defineOptions({ name: 'Chat' })
import { ref, computed, onMounted, onBeforeUnmount, onActivated, onDeactivated } from 'vue'
import { fetchPersonas } from '@/api/chat'
import { useAuthStore } from '@/stores/auth'
import { usePersonaStore } from '@/stores/persona'
import AiInputBox from '@/components/Chat/AiInputBox.vue'
import HistoryDrawer from '@/components/Chat/HistoryDrawer.vue'
import HumanThreadPanel from '@/components/Chat/HumanThreadPanel.vue'
import MessageList from '@/components/Chat/MessageList.vue'
import { stopSpeaking } from '@/utils/speech'
import { useBreakpoint } from '@/utils/useBreakpoint'
import { useChatScroll } from '@/composables/useChatScroll'
import { useChatMessages } from '@/composables/useChatMessages'
import { useChatStream } from '@/composables/useChatStream'
import { useHumanThread } from '@/composables/useHumanThread'
import { useConversationHistory } from '@/composables/useConversationHistory'

const personaStore = usePersonaStore()
const authStore = useAuthStore()

// 响应式断点：移动端抽屉全宽 / 触控目标放大等（P0-2 / P1-3）
const { isMobile } = useBreakpoint()

// 知识来源/重排分数是内部调试信息，仅 staff（admin/agent）可见；普通用户隐藏
const isStaff = computed(() => ['admin', 'agent'].includes(authStore.role))

// 人格列表加载失败只影响选择器（用空列表），不影响聊天主链路
async function loadPersonas() {
  if (personaStore.loaded) return
  try {
    const data = await fetchPersonas()
    personaStore.setPersonas(data.personas || [])
  } catch (e) {
    // 用户提示已由 request 拦截器统一处理（ElMessage）；此处补控制台观测
    console.warn('[Chat] 人格列表加载失败', e)
  }
}

// 快捷提问引导（商品咨询 / 查订单 / 物流查询 / 产品排障；退货与转人工走常驻按钮，不占建议位）
// 演示订单归属：20260801001/002/005 → user1，003/004/006 → user2；订单号跟随登录用户，
// 否则 user2 点"查订单"会因越权被拒，演示阻断
const DEMO_ORDER_BY_USER = { user2: '20260801003' }
const quickQuestions = computed(() => {
  const orderNo = DEMO_ORDER_BY_USER[authStore.user?.username] || '20260801001'
  return [
    { label: '商品咨询', text: '华为 Mate 70 Pro 多少钱', tone: 'buy' },
    { label: '查订单', text: `查一下订单 ${orderNo}`, tone: 'order' },
    { label: '物流查询', text: '物流到哪了', tone: 'logistics' },
    // 产品消歧演示入口（phase15）：同型号跨品类「S1 Pro」歧义咨询
    { label: '产品排障', text: '我的 S1 Pro 不吸了', tone: 'fix' },
  ]
})

// ---------- 会话核心状态 ----------
// 这几个 ref 被多个 composable 与子组件共用，统一在页面层创建后注入，
// 保证「一份状态」而不是每个模块各持一份副本。
const inputText = ref('')
const streaming = ref(false)
const messages = ref([])
// 当前会话 ID：继续历史会话时预设；首轮问答由 SSE start 事件采纳，
// 保证同一会话的后续提问透传同一 id（否则服务端每轮新建会话）
const currentConversationId = ref('')
// 语音播报开关（TTS 两级降级；朗读逻辑在 done 事件处理内）；UI 由 AiInputBox 渲染
const speakEnabled = ref(false)

// ---------- 组合式模块（顺序即依赖顺序，无环） ----------
// 1) 滚动跟随 + staff 实时计时
const { listRef, isAtBottom, scrollToBottom, onListScroll, syncLiveTimer, stopLiveTimer } =
  useChatScroll({ messages, streaming, isStaff })

// 2) 人工客服线程（transfer_human 成功后挂接）
const {
  humanThreadTicketId,
  humanThread,
  threadDraft,
  threadSending,
  startHumanThread,
  sendThreadReply,
  stopThreadPolling,
} = useHumanThread()

// 3) 消息工厂 + SSE 事件落账 + 反馈
const { newMessage, handleEvent, handleError, submitMessageFeedback } = useChatMessages({
  messages,
  currentConversationId,
  speakEnabled,
  startHumanThread,
})

// 4) 发送链路（客户端 history 由它持有）
const { history, trimClientHistory, onSend, stopStream, abortActive } = useChatStream({
  messages,
  streaming,
  inputText,
  currentConversationId,
  personaStore,
  isAtBottom,
  scrollToBottom,
  newMessage,
  handleEvent,
  handleError,
})

// 5) 历史会话抽屉
const {
  historyDrawerVisible,
  historyLoading,
  conversations,
  viewedMessages,
  openHistoryDrawer,
  viewConversation,
  continueConversation,
  startNewConversation,
} = useConversationHistory({
  messages,
  streaming,
  history,
  trimClientHistory,
  currentConversationId,
  newMessage,
  scrollToBottom,
})

// 当前语气（persona id → 名称；选择器与诊断面板共用）
const personaName = computed(() => {
  const id = personaStore.personaId
  if (!id) return ''
  const p = (personaStore.personas || []).find((x) => x.id === id)
  return p ? p.name : id
})

// 转人工：常驻按钮一键发起（等价于用户输入"帮我转人工客服"后发送）
function handleTransferHuman() {
  if (streaming.value) return
  inputText.value = '帮我转人工客服'
  onSend()
}

// 点击消歧候选卡片（phase15）：以该商品名称作为用户消息走既有发送链路
// （等价于用户手打该文案，下一轮经别名召回/信号词自动定向）；输入框清空由 onSend 处理
function sendDisambigChoice(candidate) {
  if (streaming.value) return
  inputText.value = candidate.name || ''
  onSend()
}

onBeforeUnmount(() => {
  stopSpeaking()
  stopLiveTimer()
  // 人工客服线程轮询：组件销毁即停止（无残留定时器）
  stopThreadPolling()
  // 组件真正销毁（如登出后布局卸载）时终止在途 SSE 流，
  // 避免旧 token 的请求在后台继续消费到结束
  abortActive()
})

onDeactivated(() => {
  // keep-alive 缓存下切走页面：停止播报与实时计时（实例保留，回来自动恢复对话现场）
  // 计时器置 null 是必须的：残留的 timer id 会让 onActivated 的 syncLiveTimer 误判
  // 「已在计时」而不重建，诊断耗时停摆
  stopSpeaking()
  stopLiveTimer()
})

onActivated(() => {
  // keep-alive 切回：流式仍在进行时恢复计时（onDeactivated 已停表）
  syncLiveTimer()
})

onMounted(() => {
  loadPersonas()
})
</script>

<style scoped>
.chat-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 20px;
  min-height: 0;
}
.panel-card {
  margin-bottom: 16px;
}
/* 对话面板撑满剩余高度：卡片与消息区做 flex 列布局 */
.chat-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.chat-panel :deep(.el-card__body) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
/* 转人工：常驻入口按钮（耳麦图标，hover 品牌蓝点亮） */
.human-btn.el-button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--am-blue-600);
  border-color: color-mix(in srgb, var(--am-blue-600) 26%, var(--am-line));
  background: var(--am-blue-50);
}
.human-btn.el-button:hover {
  color: #fff;
  border-color: var(--am-blue-600);
  background: var(--am-blue-600);
}
.card-title {
  font-weight: 600;
}
.muted-text {
  color: var(--am-text-3);
  font-size: 13px;
}

/* 消息列表 */
.chat-messages {
  flex: 1;
  min-height: 220px;
  overflow-y: auto;
  padding: 16px;
  background: var(--am-paper);
  border: 1px solid var(--am-line);
  border-radius: 10px;
}
/* 对话流限宽居中（商用聊天工作台形态） */
.chat-thread {
  max-width: 860px;
  margin: 0 auto;
}
/* 快捷提问：卡片行宽度与输入框（AiInputBox 1024px 居中）完全一致，左端对齐输入框 */
.quick-row {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  flex-wrap: wrap;
  width: 100%;
  max-width: 1024px;
  margin: 14px auto 12px;
}
.quick-caption {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.1em;
  color: var(--am-text-3);
  flex-shrink: 0;
  margin-right: 2px;
}
/* el-button 会把默认插槽包一层 span，间距用 margin 直接作用于槽内元素；
   .el-button.quick-btn 双类选择器特异性覆盖 Element 默认尺寸，免 !important（P2-2） */
.el-button.quick-btn {
  --qc: var(--el-color-primary);
  display: inline-flex;
  align-items: center;
  margin: 2px 0;
  max-width: none;
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--am-line);
  border-radius: 10px;
  background: #fff;
  transition:
    border-color 0.16s ease,
    transform 0.16s ease,
    box-shadow 0.16s ease;
}
.quick-btn:hover {
  transform: translateY(-1px);
  box-shadow: var(--am-shadow-card);
  border-color: color-mix(in srgb, var(--qc) 40%, var(--am-line));
}
.quick-btn .quick-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-right: 6px;
  background: var(--qc);
  vertical-align: middle;
}
.quick-btn .quick-label {
  color: var(--qc);
  font-weight: 600;
  flex-shrink: 0;
}
/* 类别词与示例间留出一个空格位（如「商品咨询 华为」） */
.quick-btn .quick-label + .quick-example {
  margin-left: 5px;
}
.quick-btn .quick-example {
  color: var(--am-text-2);
  font-weight: 400;
}
.quick-btn.tone-buy {
  --qc: var(--am-blue-500);
}
.quick-btn.tone-order {
  --qc: var(--am-indigo-600);
}
.quick-btn.tone-logistics {
  --qc: var(--am-teal-600);
}
.quick-btn.tone-fix {
  --qc: var(--am-amber-700);
}

/* ============ 移动端（<768px，P1-3 配套）：触控目标放大，桌面密度不变 ============ */
/* 气泡内触控目标（反馈按钮）与人工客服线程的窄屏布局随各自组件走，
   见 MessageBubble / HumanThreadPanel 的 media query */
@media (max-width: 767px) {
  .header-right .persona-select {
    width: 104px !important;
  }
  .header-right .muted-text {
    display: none; /* 窄屏收起提示文案，给操作按钮让位 */
  }
}
</style>
