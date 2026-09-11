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
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
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
          <div v-if="messages.length === 0" class="chat-empty">
            <el-empty description="您好，我是 AssistMind 智能客服，请问有什么可以帮您？" :image-size="80" />
          </div>

          <div
            v-for="m in messages"
            :key="m.id"
            class="chat-row"
            :class="m.role"
          >
          <div class="bubble" :class="m.role">
            <template v-if="m.role === 'user'">
              <div class="bubble-text">{{ m.content }}</div>
            </template>
            <template v-else>
              <!-- 流式状态 -->
              <div v-if="m.status !== 'done' && m.status !== 'error'" class="status-line">
                <el-icon class="is-loading"><Loading /></el-icon>
                <span>{{ stageText(m) }}</span>
              </div>

              <!-- 查询改写（内部机制：仅 staff 可见；普通用户只感知"正在检索"） -->
              <div v-if="isStaff && m.rewrites && m.rewrites.length" class="rewrite-line">
                <el-tag size="small" type="info" effect="plain">查询已改写</el-tag>
                <span class="muted-text">已根据您的描述扩展检索</span>
              </div>

              <!-- 工具调用过程（task 意图）：
                   过程行（工具名/参数 JSON）是内部机制，仅 staff 可见；
                   结果卡片（订单/物流/商品/售后）是业务结果，所有角色可见 -->
              <div v-if="m.toolCalls.length" class="tool-section">
                <div
                  v-for="(tc, i) in m.toolCalls"
                  :key="i"
                  class="tool-item"
                >
                  <template v-if="isStaff">
                    <el-tag size="small" type="warning">工具调用</el-tag>
                    <span class="tool-name">调用 {{ toolLabel(tc.tool_name) }}</span>
                    <span v-if="toolArgsText(tc)" class="tool-args">{{ toolArgsText(tc) }}</span>
                  </template>

                  <!-- 工具结果卡片（订单/物流/商品/售后）：白卡 + 左侧类型色条 -->
                  <div v-if="cardOf(tc)" class="tool-result-card" :class="`card-type-${cardOf(tc).type}`">
                    <!-- 订单卡片 -->
                    <template v-if="cardOf(tc).type === 'order'">
                      <el-descriptions :column="1" border size="small" class="card-desc">
                        <el-descriptions-item label="订单号">
                          {{ cardOf(tc).data.order_sn }}
                        </el-descriptions-item>
                        <el-descriptions-item label="状态">
                          {{ cardOf(tc).data.status }}
                        </el-descriptions-item>
                        <el-descriptions-item label="实付金额">
                          ¥{{ cardOf(tc).data.pay_amount }}
                        </el-descriptions-item>
                        <el-descriptions-item label="物流单号">
                          {{ cardOf(tc).data.logistics_no || '未发货' }}
                        </el-descriptions-item>
                        <el-descriptions-item label="商品明细">
                          <div v-for="(it, j) in cardOf(tc).data.items" :key="j">
                            {{ it.name }}（{{ it.spec }}）×{{ it.quantity }} ¥{{ it.price }}
                          </div>
                        </el-descriptions-item>
                      </el-descriptions>
                    </template>

                    <!-- 物流轨迹卡片 -->
                    <template v-else-if="cardOf(tc).type === 'logistics'">
                      <el-timeline class="logistics-timeline">
                        <el-timeline-item
                          v-for="(t, j) in cardOf(tc).data"
                          :key="j"
                          :timestamp="t.ts"
                          size="small"
                        >
                          {{ t.content }}
                        </el-timeline-item>
                      </el-timeline>
                    </template>

                    <!-- 商品卡片 -->
                    <template v-else-if="cardOf(tc).type === 'product'">
                      <el-descriptions :column="1" border size="small" class="card-desc">
                        <el-descriptions-item label="商品">{{ cardOf(tc).data.name }}</el-descriptions-item>
                        <el-descriptions-item label="规格">{{ cardOf(tc).data.spec }}</el-descriptions-item>
                        <el-descriptions-item label="价格">¥{{ cardOf(tc).data.price }}</el-descriptions-item>
                        <el-descriptions-item label="库存">{{ cardOf(tc).data.stock }}</el-descriptions-item>
                        <el-descriptions-item v-if="cardOf(tc).data.services && cardOf(tc).data.services.length" label="服务">
                          <el-tag
                            v-for="(s, k) in cardOf(tc).data.services"
                            :key="k"
                            size="small"
                            type="success"
                            effect="plain"
                            class="service-tag"
                          >
                            {{ s }}
                          </el-tag>
                        </el-descriptions-item>
                      </el-descriptions>
                    </template>

                    <!-- 售后结果卡片 -->
                    <template v-else-if="cardOf(tc).type === 'refund'">
                      <el-descriptions :column="1" border size="small" class="card-desc">
                        <el-descriptions-item label="售后单号">{{ cardOf(tc).data.refund_id }}</el-descriptions-item>
                        <el-descriptions-item label="状态">{{ cardOf(tc).data.status }}</el-descriptions-item>
                        <el-descriptions-item label="说明">{{ cardOf(tc).data.message }}</el-descriptions-item>
                      </el-descriptions>
                    </template>
                  </div>
                </div>
              </div>

              <!-- 工单提示 -->
              <el-alert
                v-if="m.ticketId"
                type="success"
                :closable="false"
                show-icon
                class="ticket-alert"
              >
                <template #title>
                  已创建工单
                  {{ m.ticketId }}，前往
                  <router-link to="/tickets" class="ticket-link">工单列表</router-link>
                </template>
              </el-alert>

              <!-- 回答 -->
              <div v-if="m.content" class="bubble-text md-body" v-html="renderMd(m.content)" />
              <div
                v-else-if="m.status === 'done'"
                class="bubble-text muted-text"
              >
                （暂无回答，可尝试转人工客服）
              </div>

              <!-- 诊断信息（仅 admin/agent 可见）：流式中实时展示阶段/耗时，完成后含
                   来源明细 + 事件时间线 + 阶段耗时 + 完整诊断 JSON 复制（便于定位问题） -->
              <div v-if="isStaff && (m.started || m.status === 'done')" class="diag-section">
                <button
                  type="button"
                  class="diag-header"
                  :class="{ open: m.diagOpen }"
                  @click="m.diagOpen = !m.diagOpen"
                >
                  <svg class="diag-caret" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6" /></svg>
                  <span class="diag-title">诊断信息</span>
                  <span class="diag-chips">
                    <span v-if="m.status !== 'done' && m.status !== 'error'" class="diag-chip chip-live">
                      {{ stageText(m) }} {{ fmtMs(m.elapsedMs) }}
                    </span>
                    <span class="diag-chip chip-intent">
                      {{ intentLabel(m.intent) }}<template v-if="m.routeSource"> · {{ routeLabel(m.routeSource) }}</template>
                    </span>
                    <span v-if="m.elapsedMs" class="diag-chip">{{ fmtMs(m.elapsedMs) }}</span>
                    <span v-if="m.fromCache" class="diag-chip chip-cache">缓存 {{ m.fromCache }}</span>
                    <span v-if="m.cragAction" class="diag-chip">{{ cragLabel(m.cragAction) }}</span>
                    <span v-if="m.cragScore != null && m.cragScore !== ''" class="diag-chip chip-crag">
                      CRAG {{ m.cragScore }}
                    </span>
                    <span v-if="m.degraded.length" class="diag-chip chip-degraded">
                      降级：{{ m.degraded.join('、') }}
                    </span>
                  </span>
                  <span class="diag-toggle-text">{{ m.diagOpen ? '收起' : '展开' }}</span>
                </button>
                <div v-show="m.diagOpen" class="diag-body">
                  <!-- 会话上下文：确认看到的是谁的问题、什么权限/语气在生效 -->
                  <div class="diag-row">
                    <span class="diag-row-label">当前用户</span>
                    <code class="diag-code">{{ authStore.user?.username || '—' }}</code>
                    <el-tag
                      size="small"
                      effect="plain"
                      :type="authStore.role === 'admin' ? 'danger' : 'warning'"
                    >
                      {{ authStore.role || '—' }}
                    </el-tag>
                    <span v-if="personaName" class="diag-chip">语气: {{ personaName }}</span>
                  </div>

                  <!-- 后端精确阶段耗时（faq：检索/生成/总耗时；其余意图仅前端总耗时） -->
                  <div v-if="stageBreakdown(m).length" class="diag-block">
                    <div class="diag-caption">
                      阶段耗时{{ hasBackendTimings(m) ? '（后端计时）' : '（前端由事件间隔推断）' }}
                    </div>
                    <div v-for="row in stageBreakdown(m)" :key="row[0]" class="diag-stage-row">
                      <span class="diag-stage-name">{{ row[0] }}</span>
                      <span class="diag-stage-val">{{ fmtMs(row[1]) }}</span>
                    </div>
                  </div>

                  <!-- 查询改写变体（CRAG 被动改写实际用于二次检索的词） -->
                  <div v-if="m.rewrites && m.rewrites.length" class="diag-block">
                    <div class="diag-caption">查询改写变体（二次检索用）</div>
                    <div v-for="(r, i) in m.rewrites" :key="i" class="diag-line">
                      <span class="diag-line-idx">{{ i + 1 }}</span>
                      <span class="diag-line-text">{{ r }}</span>
                    </div>
                  </div>

                  <!-- 事件时间线：每个 SSE 事件的相对时刻与间隔，定位卡在哪个环节 -->
                  <div class="diag-block" v-if="displayEvents(m).length">
                    <div class="diag-caption">事件时间线（相对发送时刻 · 间隔=与上条间隔）</div>
                    <div v-for="(e, i) in displayEvents(m)" :key="i" class="diag-line">
                      <span class="diag-line-t">{{ fmtMs(e.t) }}</span>
                      <span class="diag-line-name" :class="`evt-${e.name}`">{{ eventLabel(e.name) }}</span>
                      <span v-if="e.dt >= 100" class="diag-line-gap">+{{ fmtMs(e.dt) }}</span>
                      <span class="diag-line-text">{{ evtDataText(e) }}</span>
                    </div>
                  </div>

                  <!-- 知识来源（最终排序） -->
                  <template v-if="m.sources.length">
                    <div class="diag-row">
                      <span class="diag-row-label">知识来源（{{ m.sources.length }} 条，最终排序）</span>
                    </div>
                    <div class="diag-sources">
                      <div v-for="(s, i) in m.sources" :key="i" class="diag-source">
                        <span class="diag-source-idx">{{ i + 1 }}</span>
                        <span class="diag-source-title">{{ s.title || s.snippet || '知识库命中' }}</span>
                        <span class="diag-source-doc">{{ s.doc_id || s.source || '' }}</span>
                        <span class="diag-source-score">{{ fmtScore(s.score) }}</span>
                        <el-button
                          v-if="s.text"
                          link
                          type="primary"
                          size="small"
                          class="diag-source-toggle"
                          @click="toggleSourceDetail(m, i)"
                        >
                          {{ m.srcOpen === i ? '收起' : '溯因' }}
                        </el-button>
                        <div v-if="m.srcOpen === i" class="diag-source-detail">{{ s.text }}</div>
                      </div>
                    </div>
                  </template>
                  <div v-else-if="m.status === 'done'" class="diag-row">
                    无检索来源（chat/task 意图或缓存命中）
                  </div>

                  <div class="diag-row">
                    <span class="diag-row-label">Trace ID</span>
                    <code class="diag-code">{{ m.traceId || '—' }}</code>
                    <el-button v-if="m.traceId" link type="primary" size="small" @click="copyTrace(m.traceId)">
                      复制
                    </el-button>
                  </div>
                  <div class="diag-row">
                    <span class="diag-row-label">会话 ID</span>
                    <code class="diag-code">{{ m.conversationId || '—' }}</code>
                    <el-button v-if="m.conversationId" link type="primary" size="small" @click="copyTrace(m.conversationId)">
                      复制
                    </el-button>
                  </div>
                  <div class="diag-footer" v-if="m.status === 'done' || m.status === 'error'">
                    <el-button link type="primary" size="small" @click="copyDiagnosis(m)">
                      复制完整诊断 JSON（上报排查）
                    </el-button>
                  </div>
                </div>
              </div>

              <!-- 反馈（Bad Case 收集入口）：口袋按钮 + 内联评论行 -->
              <div v-if="m.status === 'done' && !m.isNotice" class="feedback-row">
                <template v-if="!m.feedbackSubmitted">
                  <div class="fb-group">
                    <button
                      class="fb-btn"
                      :class="{ 'is-active': m.feedbackScore === 5 }"
                      :title="m.feedbackScore === 5 ? '已选' : '有帮助'"
                      @click="m.feedbackScore = 5"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
                    </button>
                    <button
                      class="fb-btn fb-btn-down"
                      :class="{ 'is-active': m.feedbackScore === 1 }"
                      :title="m.feedbackScore === 1 ? '已选' : '没帮助'"
                      @click="m.feedbackScore = 1"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7 0h3a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-3"/></svg>
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
                      @click="submitMessageFeedback(m)"
                    >
                      提交
                    </button>
                  </div>
                </template>
                <span v-else class="muted-text">已提交反馈，感谢您的反馈！</span>
              </div>

              <!-- 错误提示 -->
              <el-alert
                v-if="m.status === 'error'"
                type="error"
                :closable="false"
                show-icon
                class="error-alert"
                :title="m.errorMsg || '服务异常，请重试'"
              />
            </template>
          </div>
        </div>
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

    <!-- 历史会话抽屉：列出本人会话（updated_at 倒序），支持查看消息与继续对话；
         移动端（<768px）全屏打开避免 380px 固定宽度溢出小屏（P0-2） -->
    <el-drawer
      v-model="historyDrawerVisible"
      title="历史会话"
      :size="isMobile ? '100%' : '380px'"
      class="history-drawer"
    >
      <div class="history-toolbar">
        <el-button size="small" type="primary" plain @click="startNewConversation">
          新对话
        </el-button>
      </div>

      <div v-if="historyLoading" class="history-loading muted-text">加载中…</div>
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
            <el-button size="small" link type="primary" @click="viewConversation(c)">查看</el-button>
            <el-button size="small" link type="success" @click="continueConversation(c)">
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
  </div>
</template>

<script setup>
// 显式命名：MainLayout 的 <keep-alive include="Chat"> 按组件名缓存实例，
// 避免从聊天页跳转工单/订单等页面后对话（组件内存 ref）随卸载全部丢失
defineOptions({ name: 'Chat' })
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount, onDeactivated } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { ElMessage } from 'element-plus'
import { chatStream, fetchPersonas } from '@/api/chat'
import { listConversations, listMessages } from '@/api/conversation'
import { submitFeedback } from '@/api/feedback'
import { useAuthStore } from '@/stores/auth'
import { usePersonaStore } from '@/stores/persona'
import AiInputBox from '@/components/Chat/AiInputBox.vue'
import { speak, stopSpeaking } from '@/utils/speech'
import { useBreakpoint } from '@/utils/useBreakpoint'

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

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

// 快捷提问引导（商品咨询 / 查订单 / 物流查询；退货与转人工走常驻按钮，不占建议位）
// 演示订单归属：20260801001/002 → user1，003/004 → user2；订单号跟随登录用户，
// 否则 user2 点"查订单"会因越权被拒，演示阻断
const DEMO_ORDER_BY_USER = { user2: '20260801003' }
const quickQuestions = computed(() => {
  const orderNo = DEMO_ORDER_BY_USER[authStore.user?.username] || '20260801001'
  return [
    { label: '商品咨询', text: '华为 Mate 70 Pro 多少钱', tone: 'buy' },
    { label: '查订单', text: `查一下订单 ${orderNo}`, tone: 'order' },
    { label: '物流查询', text: '物流到哪了', tone: 'logistics' },
  ]
})

// 流式阶段 → 展示文案（对齐后端 SSE 事件；tool 为业务工具处理中，普通用户友好提示）
const STAGE_TEXT = {
  start: '正在思考…',
  retrieving: '正在检索知识库…',
  tool: '正在为您处理，请稍候…',
  generating: '正在生成回答…',
}

// 工具名 → 中文标签
const TOOL_LABELS = {
  search_knowledge: '检索知识库',
  create_ticket: '创建工单',
  transfer_human: '转人工客服',
  get_ticket_status: '查询工单状态',
  apply_refund: '申请退款',
  query_order: '查询订单',
  query_logistics: '查询物流',
  query_product: '查询商品',
}

const inputText = ref('')
const streaming = ref(false)
const messages = ref([])
// 已完成轮次的对话历史 [{role: 'user'|'assistant', content}]，随请求发给后端
const history = ref([])
const listRef = ref(null)

// ---------- 历史会话抽屉 ----------
const historyDrawerVisible = ref(false)
const historyLoading = ref(false)
// 会话列表（本人，updated_at 倒序）
const conversations = ref([])
// 「查看」模式：抽屉内下方展开的会话消息（正序）
const viewedMessages = ref([])
// 当前会话 ID：继续历史会话时预设；首轮问答由 SSE start 事件采纳，
// 保证同一会话的后续提问透传同一 id（否则服务端每轮新建会话）
const currentConversationId = ref('')

// ---------- 语音播报开关（TTS 两级降级；朗读逻辑见 done 事件处理） ----------
// 播报状态保留在页面层（done 事件朗读判断用），UI 由 AiInputBox 渲染
const speakEnabled = ref(false)

onBeforeUnmount(() => {
  stopSpeaking()
  if (liveTimer) clearInterval(liveTimer)
  // 组件真正销毁（如登出后布局卸载）时终止在途 SSE 流，
  // 避免旧 token 的请求在后台继续消费到结束
  activeAbort?.abort()
})

onDeactivated(() => {
  // keep-alive 缓存下切走页面：停止播报与实时计时（实例保留，回来自动恢复对话现场）
  stopSpeaking()
  if (liveTimer) clearInterval(liveTimer)
})

let seq = 0
function nextId() {
  return `msg-${++seq}-${Date.now()}`
}

function newMessage(role, content) {
  return {
    id: nextId(),
    role,
    content,
    status: role === 'user' ? 'done' : 'thinking',
    stage: 'start',
    intent: '',
    rewrites: [],
    toolCalls: [],
    sources: [],
    ticketId: '',
    errorMsg: '',
    // Bad Case 闭环：会话/trace 关联 + 反馈状态 + 追溯快照
    conversationId: '',
    traceId: '',
    cragAction: '',
    cragScore: '',
    degraded: [],
    timings: {},
    // 管理员/客服诊断展示
    sentAt: 0,
    elapsedMs: 0,
    routeSource: '',
    fromCache: '',
    // 事件时间线：start 事件已到（流式实时展示诊断），SSE 事件快照与阶段时刻
    started: false,
    eventLog: [],
    stageTs: {},
    deltaCount: 0,
    deltaChars: 0,
    deltaFirstMs: 0,
    deltaLastMs: 0,
    // 诊断信息默认展开（admin/agent 点收起）
    diagOpen: true,
    srcOpen: -1,
    feedbackScore: 0,
    feedbackComment: '',
    feedbackSubmitted: false,
    feedbackSubmitting: false,
    // 系统提示消息（如"已切换到历史会话"）：不显示反馈条
    isNotice: false,
  }
}

function stageText(m) {
  return STAGE_TEXT[m.stage] || '正在思考…'
}

function toolLabel(name) {
  return TOOL_LABELS[name] || name
}

function toolArgsText(tc) {
  if (!tc.arguments) return ''
  let text = ''
  try {
    text = JSON.stringify(tc.arguments)
  } catch (e) {
    text = String(tc.arguments)
  }
  return text.length > 80 ? `${text.slice(0, 80)}…` : text
}

// 工具结果 → 卡片结构化数据（订单/物流/商品/售后）；不可解析返回 null（回退 JSON 文本）
function parseToolResult(tc) {
  const name = tc.tool_name
  const result = tc.result
  if (result === undefined || result === null) return null
  // MCP 工具返回可能是 {result: ...} 包装，也可能是直接 dict/list
  const data = result && typeof result === 'object' && 'result' in result ? result.result : result

  if (name === 'query_order' && data && typeof data === 'object' && !Array.isArray(data) && data.order_sn) {
    return { type: 'order', data }
  }
  if (name === 'query_logistics' && Array.isArray(data) && data.length && data[0].ts) {
    return { type: 'logistics', data }
  }
  if (name === 'query_product' && data && typeof data === 'object' && !Array.isArray(data) && data.id) {
    return { type: 'product', data }
  }
  if (name === 'apply_refund' && data && typeof data === 'object' && !Array.isArray(data) && data.refund_id) {
    return { type: 'refund', data }
  }
  return null
}

// 惰性缓存解析结果（模板中多次引用只算一次）。
// 关键：SSE 的 tool_call 先到、tool_result 后到——result 从 undefined 变为对象时
// 必须失效重算，否则先渲染出的 _card=null 会永久缓存，卡片永不出现（真实时序 bug）
function cardOf(tc) {
  if (tc._cardKey !== tc.result) {
    tc._cardKey = tc.result
    tc._card = parseToolResult(tc)
  }
  return tc._card
}

// 从工具结果中提取工单号（MCP create_ticket / transfer_human 返回 {ticket_id}）
function extractTicketId(result) {
  if (!result) return ''
  if (typeof result === 'object' && result.ticket_id) return result.ticket_id
  if (typeof result === 'string') {
    const m = result.match(/TK-\d{7,}/)
    if (m) return m[0]
  }
  return ''
}

function renderMd(text) {
  if (!text) return ''
  // markdown 渲染结果经 DOMPurify 消毒后再注入（LLM/知识库内容不可信，
  // 防 javascript: 链接、事件属性等 XSS 载荷）
  return DOMPurify.sanitize(md.render(text))
}

// 来源分数展示（保留 2 位小数）
function fmtScore(score) {
  const n = Number(score)
  return Number.isFinite(n) ? n.toFixed(2) : ''
}

// ---------- 管理员/客服诊断辅助 ----------

const INTENT_LABELS = {
  faq: '知识问答',
  task: '任务处理',
  chat: '闲聊',
  unclear: '意图不明',
}

const ROUTE_LABELS = {
  rule: '规则命中',
  semantic: '语义命中',
  llm: 'LLM 分类',
  fallback: '降级兜底',
}

const CRAG_LABELS = {
  generate: 'CRAG: 直接生成',
  rewrite_retry: 'CRAG: 改写重试',
  no_result: 'CRAG: 无结果',
}

function intentLabel(intent) {
  return INTENT_LABELS[intent] || intent || '—'
}

function routeLabel(source) {
  return ROUTE_LABELS[source] || source || ''
}

function cragLabel(action) {
  return CRAG_LABELS[action] || (action ? action.toUpperCase() : '')
}

function fmtMs(ms) {
  const n = Number(ms)
  if (!Number.isFinite(n) || n < 0) return ''
  return n >= 10000 ? `${(n / 1000).toFixed(1)}s` : `${n}ms`
}

function copyTrace(text) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).catch(() => {})
  }
}

// 展开/收起某条检索来源的片段全文（查看溯因）
function toggleSourceDetail(m, i) {
  m.srcOpen = m.srcOpen === i ? -1 : i
}

// ---------- 诊断增强：事件时间线 / 阶段耗时 / 完整诊断 JSON ----------

// 后端 faq 阶段计时（毫秒）存在时优先展示（网络层耗时不可靠）
function hasBackendTimings(m) {
  const o = m.timings || {}
  return o.total_ms != null || o.retrieve_ms != null || o.generate_ms != null
}

function summarize(v, max = 200) {
  if (v === undefined || v === null) return v
  let s
  try {
    s = typeof v === 'string' ? v : JSON.stringify(v)
  } catch {
    s = String(v)
  }
  return s.length > max ? `${s.slice(0, max)}…` : s
}

// 事件数据快照（截断大对象，避免诊断面板堆积噪声）
function eventSnap(name, data) {
  const d = data || {}
  switch (name) {
    case 'tool_result':
      return { tool_name: d.tool_name, result: summarize(d.result, 160) }
    case 'tool_call':
      return { tool_name: d.tool_name, arguments: summarize(d.arguments, 120) }
    case 'start':
      return {
        intent: d.intent,
        route_source: d.route_source,
        role: d.role,
        conversation_id: d.conversation_id,
      }
    case 'rewriting':
      return { variants: Array.isArray(d.variants) ? d.variants : [] }
    case 'done':
      return {
        from_cache: d.from_cache,
        crag_action: d.crag_action,
        crag_score: d.crag_score,
        degraded: d.degraded,
        timings: d.timings,
      }
    default:
      return d
  }
}

// 记录 SSE 事件到诊断时间线（delta 聚合为计数，不进列表防刷屏）
function recordEvent(msg, name, data) {
  if (!msg.sentAt) msg.sentAt = Date.now()
  const t = Date.now() - msg.sentAt
  msg.elapsedMs = t
  if (name === 'delta') {
    msg.deltaCount += 1
    msg.deltaChars += data && data.delta ? data.delta.length : 0
    if (!msg.deltaFirstMs) msg.deltaFirstMs = t
    msg.deltaLastMs = t
    return
  }
  const last = msg.eventLog[msg.eventLog.length - 1]
  msg.eventLog.push({ name, t, dt: last ? t - last.t : 0, data: eventSnap(name, data) })
  if (!(name in msg.stageTs)) msg.stageTs[name] = t
}

// 诊断时间线：事件快照按时刻正序（delta 摘要插到实际发生位置），间隔重算
function displayEvents(m) {
  const evs = m.eventLog.map((e) => ({ ...e }))
  if (m.deltaCount) {
    evs.push({
      name: 'delta',
      t: m.deltaLastMs,
      dt: 0,
      data: { count: m.deltaCount, chars: m.deltaChars, firstMs: m.deltaFirstMs },
    })
    evs.sort((a, b) => a.t - b.t)
  }
  evs.forEach((e, i) => {
    e.dt = i > 0 ? e.t - evs[i - 1].t : 0
  })
  return evs
}

const EVENT_LABELS = {
  start: '开始',
  retrieving: '检索',
  rewriting: '改写',
  generating: '生成中',
  delta: '流式输出',
  tool_call: '工具调用',
  tool_result: '工具结果',
  done: '完成',
  error: '错误',
}

function eventLabel(name) {
  return EVENT_LABELS[name] || name
}

function evtDataText(e) {
  const d = e.data || {}
  switch (e.name) {
    case 'start':
      return (
        `意图: ${intentLabel(d.intent)}` +
        (d.route_source ? ` · ${routeLabel(d.route_source)}` : '') +
        (d.role ? ` · role=${d.role}` : '')
      )
    case 'tool_call':
      return `tool: ${d.tool_name || ''} args=${summarize(d.arguments, 60)}`
    case 'tool_result':
      return `${d.tool_name || ''} => ${summarize(d.result, 60)}`
    case 'rewriting':
      return `变体×${(d.variants || []).length}`
    case 'delta':
      return `×${d.count} 块 / ${d.chars} 字符（首块 ${fmtMs(d.firstMs)}）`
    case 'done':
      return (
        `cache: ${d.from_cache || '—'} · crag: ${d.crag_action || '—'} · ` +
        (Array.isArray(d.degraded) && d.degraded.length
          ? `降级: ${d.degraded.join('、')}`
          : '无降级')
      )
    case 'error':
      return `msg: ${d.message || ''}`
    default:
      return ''
  }
}

// 阶段耗时（ms）：后端 timings 优先，否则由事件相对时刻差推断（faq 链路为主）
function stageBreakdown(m) {
  const o = m.timings || {}
  if (hasBackendTimings(m)) {
    const rows = []
    if (o.retrieve_ms != null) rows.push(['检索', o.retrieve_ms])
    if (o.generate_ms != null) rows.push(['生成', o.generate_ms])
    if (o.total_ms != null) rows.push(['后端总耗时', o.total_ms])
    return rows
  }
  const st = m.stageTs
  const chain = [
    ['start', '路由'],
    ['retrieving', '检索'],
    ['rewriting', '改写'],
    ['generating', '生成'],
    ['done', '完成'],
  ]
  const rows = []
  for (let i = 1; i < chain.length; i += 1) {
    const cur = st[chain[i][0]]
    const prev = st[chain[i - 1][0]]
    if (cur != null && prev != null) rows.push([chain[i][1], cur - prev])
  }
  if (m.elapsedMs) rows.push(['前端总耗时', m.elapsedMs])
  return rows
}

// 完整诊断 JSON：admin/agent 一键复制上报排查
function diagBlob(m) {
  const idx = messages.value.indexOf(m)
  const userMsg = idx > 0 ? messages.value[idx - 1] : null
  return JSON.stringify(
    {
      user: authStore.user?.username || '',
      role: authStore.role || '',
      ask_time: m.sentAt ? new Date(m.sentAt).toISOString() : '',
      total_ms: m.elapsedMs || '',
      intent: m.intent || '',
      route_source: m.routeSource || '',
      persona: personaStore.personaId || '',
      from_cache: m.fromCache || '',
      crag_action: m.cragAction || '',
      crag_score: m.cragScore || '',
      degraded: m.degraded || [],
      rewrites: m.rewrites || [],
      backend_timings_ms: m.timings || {},
      query: userMsg ? userMsg.content : '',
      answer: m.content || '',
      sources: m.sources || [],
      tools: m.toolCalls.map((tc) => ({
        name: tc.tool_name,
        args: summarize(tc.arguments, 400),
        result: summarize(tc.result, 800),
      })),
      events: displayEvents(m).map((e) => ({
        name: e.name,
        t_ms: e.t,
        gap_ms: e.dt,
        data: e.data,
      })),
      error: m.errorMsg || '',
      trace_id: m.traceId || '',
      conversation_id: m.conversationId || '',
    },
    null,
    2
  )
}

function copyDiagnosis(m) {
  copyTrace(diagBlob(m))
}

// 当前语气（persona id → 名称；选择器与诊断面板共用）
const personaName = computed(() => {
  const id = personaStore.personaId
  if (!id) return ''
  const p = (personaStore.personas || []).find((x) => x.id === id)
  return p ? p.name : id
})

// ---------- 历史会话抽屉 ----------

// 后端返回 ISO 时间（naive UTC）→ 'YYYY-MM-DD HH:mm' 展示
function fmtTime(iso) {
  return String(iso || '').replace('T', ' ').slice(0, 16)
}

async function openHistoryDrawer() {
  historyDrawerVisible.value = true
  // 每次打开都拉最新列表（新完成的问答会浮到最上）
  await loadConversations()
}

async function loadConversations() {
  historyLoading.value = true
  try {
    const data = await listConversations()
    conversations.value = data.conversations || []
  } catch (e) {
    // 错误提示已由 request 拦截器统一处理；列表置空走抽屉空态
    conversations.value = []
    console.warn('[Chat] 会话列表加载失败', e)
  } finally {
    historyLoading.value = false
  }
}

// 「查看」：抽屉内下方展开该会话消息（正序）
async function viewConversation(c) {
  viewedMessages.value = []
  try {
    const data = await listMessages(c.id)
    viewedMessages.value = data.messages || []
  } catch (e) {
    // 错误提示已由 request 拦截器统一处理；保持空列表
    console.warn('[Chat] 会话消息加载失败', e)
  }
}

// 「继续对话」：重建 history、归属原会话，主消息区清空并插入提示
async function continueConversation(c) {
  // 流式进行中禁止切换：否则旧流的 start 事件会把自己的 conversation_id
  // 误采纳为当前会话，旧 done 还会污染刚清空的 history（并发竞态）
  if (streaming.value) return
  let rebuilt = []
  try {
    const data = await listMessages(c.id)
    rebuilt = (data.messages || [])
      .filter((m) => m && m.content)
      .map((m) => ({ role: m.role, content: m.content }))
  } catch (e) {
    // 拉取失败仍切换会话（后续请求带 conversation_id，服务端保存有消息），
    // history 置空仅影响本地上下文，不阻塞切换
    console.warn('[Chat] 会话消息重建失败，仅切换会话', e)
  }
  history.value = rebuilt
  currentConversationId.value = c.id
  historyDrawerVisible.value = false
  viewedMessages.value = []
  messages.value = []
  const tip = newMessage('assistant', '已切换到历史会话，可继续提问')
  tip.status = 'done'
  tip.stage = 'done'
  tip.isNotice = true
  messages.value.push(tip)
  scrollToBottom()
}

// 「新对话」：清空历史/当前会话/消息列表，回到空态
function startNewConversation() {
  // 流式进行中禁止清空：currentConversationId 置空会让在途流的 start 事件
  // 被误采纳为新会话 id，旧问答也会写进新会话上下文（并发竞态）
  if (streaming.value) return
  history.value = []
  currentConversationId.value = ''
  viewedMessages.value = []
  messages.value = []
  historyDrawerVisible.value = false
}

// 处理一条 SSE 事件，更新对应客服消息
function handleEvent(msg, name, data) {
  // 诊断时间线：每条事件落账（含相对时刻/间隔），delta 走聚合计数
  recordEvent(msg, name, data)
  switch (name) {
    case 'start':
      msg.started = true
      msg.intent = data.intent || ''
      msg.routeSource = data.route_source || ''
      msg.stage = 'start'
      // 会话 ID：每条问答唯一，反馈关联 Bad Case 用
      msg.conversationId = data.conversation_id || msg.conversationId || ''
      // 会话归属：currentConversationId 为空（新会话首轮）时采纳服务端分配的
      // conversation_id，后续提问透传同一 id，保证多轮归属同一会话
      if (!currentConversationId.value && data.conversation_id) {
        currentConversationId.value = data.conversation_id
      }
      break
    case 'retrieving':
      msg.stage = 'retrieving'
      break
    case 'rewriting':
      msg.rewrites = data.variants || []
      msg.stage = 'generating'
      break
    case 'generating':
      msg.stage = 'generating'
      break
    case 'delta':
      // 流式生成：逐 chunk 累积文本（打字机效果）；done 事件会带完整 answer 兜底
      msg.stage = 'generating'
      msg.content = (msg.content || '') + (data.delta || '')
      break
    case 'tool_call':
      // 阶段进入业务处理中（普通用户显示友好文案，不再停留在"正在思考"）
      msg.stage = 'tool'
      msg.toolCalls.push({
        tool_name: data.tool_name || '',
        arguments: data.arguments || {},
        ticketId: '',
      })
      break
    case 'tool_result': {
      const tc = msg.toolCalls[msg.toolCalls.length - 1]
      if (tc) {
        // 保存工具结果（订单/物流/商品/退款卡片渲染用）
        tc.result = data.result
        const ticketId = extractTicketId(data.result)
        if (ticketId) {
          tc.ticketId = ticketId
          msg.ticketId = ticketId
        }
      }
      break
    }
    case 'done':
      msg.stage = 'done'
      msg.status = 'done'
      msg.content = data.answer || ''
      // 总耗时 = 发送时刻 → done 时刻（前端计时，覆盖 chat/faq/task 三类意图）
      if (msg.sentAt) msg.elapsedMs = Date.now() - msg.sentAt
      msg.fromCache = data.from_cache || ''
      if (Array.isArray(data.sources)) msg.sources = data.sources
      msg.conversationId = data.conversation_id || msg.conversationId || ''
      msg.traceId = data.trace_id || ''
      // 追溯快照：CRAG 决策与降级项（提交反馈随 payload 入库）
      msg.cragAction = data.crag_action || ''
      // 诊断：CRAG 评估分数 + 后端阶段耗时（faq 检索/生成/总耗时）
      msg.cragScore = data.crag_score != null && data.crag_score !== '' ? data.crag_score : ''
      msg.timings = data.timings && typeof data.timings === 'object' ? data.timings : {}
      msg.degraded = Array.isArray(data.degraded) ? data.degraded : []
      if (data.ticket_id) msg.ticketId = data.ticket_id
      // 语音播报：开启开关时朗读最终答案（纯文本，markdown 源串朗读）
      if (speakEnabled.value && msg.content) speak(msg.content)
      break
    default:
      break
  }
}

function handleError(msg, message) {
  if (msg.status === 'done') return
  msg.status = 'error'
  msg.stage = 'error'
  msg.errorMsg = message || '服务异常，请稍后重试'
}

// 提交本条问答的满意度反馈（Bad Case 收集入口）
async function submitMessageFeedback(m) {
  if (m.feedbackSubmitting) return
  // 未打分：显式提示而不是静默置灰（用户不知道按钮为什么点不动）
  if (!m.feedbackScore) {
    ElMessage.warning('请先选择"有帮助"或"没帮助"再提交')
    return
  }
  // 用户问题：本条客服消息的前一条用户消息
  const idx = messages.value.indexOf(m)
  const userMsg = idx > 0 ? messages.value[idx - 1] : null
  m.feedbackSubmitting = true
  try {
    await submitFeedback({
      score: m.feedbackScore,
      comment: m.feedbackComment || '',
      conversation_id: m.conversationId || '',
      trace_id: m.traceId || '',
      query: userMsg ? userMsg.content : '',
      answer: m.content || '',
      sources: m.sources || [],
      intent: m.intent || '',
      crag_action: m.cragAction || '',
      degraded: m.degraded || [],
    })
    m.feedbackSubmitted = true
    ElMessage.success('感谢您的反馈！')
  } catch (e) {
    // 用户提示已由 request 拦截器统一处理（ElMessage）；此处补控制台观测
    console.warn('[Chat] 反馈提交失败', e)
  } finally {
    m.feedbackSubmitting = false
  }
}

// 转人工：常驻按钮一键发起（等价于用户输入"帮我转人工客服"后发送）
function handleTransferHuman() {
  if (streaming.value) return
  inputText.value = '帮我转人工客服'
  onSend()
}

// 当前在途流的 AbortController + 是否由用户手动停止（停止不是错误，气泡正常收尾）
let activeAbort = null
let stoppedByUser = false

// 停止生成：中止在途 SSE 流，保留已输出内容（管理员/用户不想等长回答时用）
function stopStream() {
  if (!streaming.value) return
  stopSpeaking()
  stoppedByUser = true
  activeAbort?.abort()
}

async function onSend() {
  const q = inputText.value.trim()
  if (!q || streaming.value) return
  // 新提问到达：打断仍在进行的上一轮播报
  stopSpeaking()
  const assistantMsg = newMessage('assistant', '')
  assistantMsg.sentAt = Date.now()
  messages.value.push(newMessage('user', q), assistantMsg)
  // 关键：后续 SSE 事件写入必须走「响应式代理版」消息对象。
  // newMessage() 返回原始对象，push 后 Vue 已将其转为 reactive proxy——
  // 继续用 raw 对象写入不会触发更新（tool_result 到达后卡片依赖 done 兜底渲染，
  // 行为脆弱且不符合直觉）。代理引用保证每条事件写入即时派发重渲染。
  const liveMsg = messages.value[messages.value.length - 1]
  inputText.value = ''
  streaming.value = true
  stoppedByUser = false
  activeAbort = new AbortController()
  // 用户主动发言：无条件回底（并恢复贴底跟随）
  isAtBottom.value = true
  scrollToBottom()
  try {
    await chatStream(q, {
      history: history.value.slice(),
      persona: personaStore.personaId || null,
      // 继续历史会话时非空：该轮消息归属原会话；新会话为空，由 start 事件采纳
      conversationId: currentConversationId.value,
      // 停止生成：中止信号（fetch 收到 AbortError），已生成内容保留
      signal: activeAbort.signal,
      onEvent: (name, data) => {
        handleEvent(liveMsg, name, data)
        if (name === 'done') {
          history.value.push({ role: 'user', content: q })
          history.value.push({ role: 'assistant', content: liveMsg.content || '' })
        }
      },
      onDone: () => {
        streaming.value = false
        activeAbort = null
      },
      onError: (message) => {
        streaming.value = false
        activeAbort = null
        if (stoppedByUser) {
          // 用户手动停止：保留已生成内容并正常收尾，不显示错误
          liveMsg.status = 'done'
          liveMsg.stage = 'done'
          return
        }
        handleError(liveMsg, message)
      },
    })
  } catch (e) {
    // chatStream 内部已上报错误（reported 守卫），此处仅兜底清理状态
    streaming.value = false
    activeAbort = null
    if (stoppedByUser) {
      liveMsg.status = 'done'
      liveMsg.stage = 'done'
      return
    }
    handleError(liveMsg, (e && e.message) || '网络请求失败')
  }
}

function scrollToBottom() {
  nextTick(() => {
    const el = listRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// 贴底才跟随（P1-4）：流式期间用户上翻阅读时不再被强制拽回底部；
// 滚回底部附近（<40px）自动恢复跟随
const isAtBottom = ref(true)
function onListScroll() {
  const el = listRef.value
  if (!el) return
  isAtBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

watch(messages, () => {
  if (isAtBottom.value) scrollToBottom()
}, { deep: true })

// 流式进行中：每 500ms 刷新诊断面板的实时耗时（staff 定位"卡住"环节用）；
// 结束（done/error/异常）即停表，避免空跑
let liveTimer = null
watch(streaming, (v) => {
  if (v && isStaff.value) {
    clearInterval(liveTimer)
    liveTimer = setInterval(() => {
      const now = Date.now()
      messages.value.forEach((m) => {
        if (m.started && m.status !== 'done' && m.status !== 'error' && m.sentAt) {
          m.elapsedMs = now - m.sentAt
        }
      })
    }, 500)
  } else if (liveTimer) {
    clearInterval(liveTimer)
    liveTimer = null
  }
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
.chat-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  min-height: 240px;
}
.chat-row {
  display: flex;
  margin-bottom: 12px;
}
.chat-row.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.65;
  word-break: break-word;
}
.bubble.user {
  background: linear-gradient(135deg, var(--am-blue-600), var(--am-blue-500));
  color: #fff;
  border-top-right-radius: 4px;
  box-shadow: 0 2px 8px rgba(35, 82, 197, 0.22);
}
.bubble.assistant {
  background: var(--am-card);
  color: var(--am-text);
  border: 1px solid var(--am-line);
  box-shadow: 0 1px 3px rgba(14, 33, 64, 0.05);
  border-top-left-radius: 4px;
  font-size: 14.5px;
  line-height: 1.7;
}
.bubble-text {
  white-space: pre-wrap;
}
.bubble.user .bubble-text {
  white-space: normal;
}

/* 状态行 */
.status-line {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--am-text-3);
  font-size: 13px;
}
.rewrite-line {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 0;
  font-size: 13px;
}

/* 工具调用 */
.tool-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 6px 0;
}
.tool-item {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 13px;
}
.tool-name {
  font-weight: 600;
  color: var(--am-text);
}
.tool-args {
  color: var(--am-text-3);
  font-size: 12px;
  font-family: var(--am-font-mono);
  word-break: break-all;
}

/* 工具结果卡片：白卡 + 左侧类型色条（订单蓝 / 物流青 / 商品靛 / 退款玫红） */
.tool-result-card {
  position: relative;
  margin-top: 6px;
  padding: 10px 12px 10px 14px;
  background: #fff;
  border: 1px solid var(--am-line);
  border-radius: 8px;
  width: 100%;
  overflow: hidden;
}
.tool-result-card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 2px;
  background: var(--am-blue-600);
}
.tool-result-card.card-type-order::before {
  background: var(--am-blue-600);
}
.tool-result-card.card-type-logistics::before {
  background: var(--am-teal-600);
}
.tool-result-card.card-type-product::before {
  background: var(--am-indigo-600);
}
.tool-result-card.card-type-refund::before {
  background: var(--am-rose-600);
}
.card-desc {
  width: 100%;
}
.card-desc :deep(.el-descriptions__label) {
  color: var(--am-text-3);
  font-weight: 500;
  background: var(--am-paper);
}
.logistics-timeline {
  padding-left: 4px;
}
.service-tag {
  margin-right: 4px;
}

/* 诊断信息面板（仅 admin/agent 可见）：意图/路由/耗时/缓存/CRAG/降级 + 时间线 + 来源 */
.diag-section {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--am-line);
}
.diag-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 4px 0;
  border: none;
  background: none;
  cursor: pointer;
  font: inherit;
  color: var(--am-text-2);
  text-align: left;
  flex-wrap: wrap;
}
.diag-header:hover .diag-title {
  color: var(--am-blue-600);
}
.diag-caret {
  color: var(--am-text-3);
  flex-shrink: 0;
  transition: transform 0.18s ease;
}
.diag-header.open .diag-caret {
  transform: rotate(90deg);
}
.diag-title {
  font-weight: 600;
  color: var(--am-text-2);
  font-size: 13px;
  flex-shrink: 0;
}
.diag-chips {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
}
.diag-chip {
  display: inline-block;
  padding: 0 8px;
  height: 20px;
  line-height: 20px;
  border-radius: 10px;
  font-size: 12px;
  background: var(--am-blue-50);
  color: var(--am-text-2);
  white-space: nowrap;
}
.diag-chip.chip-intent {
  background: var(--am-blue-600);
  color: #fff;
  font-weight: 600;
}
.diag-chip.chip-cache {
  background: #ecfdf5;
  color: var(--am-green-700);
}
.diag-chip.chip-degraded {
  background: #fef2f2;
  color: var(--am-red-700);
}
.diag-chip.chip-crag {
  background: #f5f3ff;
  color: var(--am-violet-700);
}
.diag-chip.chip-live {
  background: var(--am-blue-50);
  color: var(--am-blue-600);
  animation: diag-pulse 1.4s ease-in-out infinite;
}
@keyframes diag-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.55;
  }
}
.diag-toggle-text {
  font-size: 12px;
  color: var(--am-text-3);
  flex-shrink: 0;
}
.diag-body {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.diag-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  flex-wrap: wrap;
}
.diag-row-label {
  font-weight: 600;
  color: var(--am-text-2);
  font-size: 13px;
}
.diag-code {
  background: var(--am-blue-50);
  border-radius: 3px;
  padding: 1px 6px;
  font-size: 12px;
  font-family: var(--am-font-mono);
  word-break: break-all;
  color: var(--am-text-2);
}
.diag-block {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.diag-caption {
  font-size: 12px;
  font-weight: 600;
  color: var(--am-text-3);
}
.diag-stage-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}
.diag-stage-name {
  width: 76px;
  flex-shrink: 0;
  color: var(--am-text-2);
}
.diag-stage-val {
  font-family: var(--am-font-mono);
  color: var(--am-text);
}
.diag-line {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  line-height: 1.6;
}
.diag-line-idx {
  color: var(--am-blue-600);
  flex-shrink: 0;
  min-width: 14px;
}
.diag-line-t {
  font-family: var(--am-font-mono);
  color: var(--am-text-3);
  flex-shrink: 0;
  min-width: 44px;
}
.diag-line-name {
  flex-shrink: 0;
  font-weight: 600;
  color: var(--am-text-2);
}
.diag-line-name.evt-start {
  color: var(--am-blue-600);
}
.diag-line-name.evt-done {
  color: var(--am-green-700);
}
.diag-line-name.evt-error {
  color: var(--am-red-700);
}
.diag-line-name.evt-tool_call,
.diag-line-name.evt-tool_result {
  color: var(--am-amber-700);
}
.diag-line-gap {
  flex-shrink: 0;
  color: var(--am-red-600);
  font-family: var(--am-font-mono);
  font-size: 11px;
}
.diag-line-text {
  color: var(--am-text-2);
  word-break: break-all;
  min-width: 0;
}
.diag-sources {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.diag-source {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--am-text-2);
  padding: 2px 0;
  flex-wrap: wrap;
}
.diag-source-idx {
  color: var(--am-blue-600);
  flex-shrink: 0;
}
.diag-source-title {
  word-break: break-all;
}
.diag-source-doc {
  color: var(--am-text-3);
  font-size: 12px;
  font-family: var(--am-font-mono);
  word-break: break-all;
}
.diag-source-score {
  color: var(--am-text-2);
  font-size: 12px;
  flex-shrink: 0;
}
.diag-source-toggle {
  flex-shrink: 0;
}
.diag-source-detail {
  width: 100%;
  margin-top: 2px;
  padding: 6px 8px;
  background: var(--am-blue-50);
  border-radius: 4px;
  color: var(--am-text-2);
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
.diag-footer {
  display: flex;
  justify-content: flex-end;
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
  transition: background 0.16s ease, color 0.16s ease, transform 0.16s ease;
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
  transition: background 0.16s ease, color 0.16s ease;
}
.fb-submit:hover {
  background: var(--am-blue-600);
  color: #fff;
}
.fb-submit.is-loading {
  opacity: 0.6;
  pointer-events: none;
}

/* 工单 / 诊断提示 */
.ticket-alert {
  margin: 8px 0 4px;
}
.ticket-link {
  color: var(--am-blue-600);
  text-decoration: none;
}
.error-alert {
  margin-top: 6px;
}

/* markdown 回答 */
.md-body :deep(p) {
  margin: 4px 0;
}
.md-body :deep(pre) {
  background: var(--am-blue-50);
  border-radius: 4px;
  padding: 8px;
  overflow-x: auto;
  font-size: 12px;
}
.md-body :deep(code) {
  background: var(--am-blue-50);
  border-radius: 3px;
  padding: 1px 4px;
  font-size: 12px;
}
.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
/* LLM 输出的标题/表格/引用块（P2-1）：此前未样式化会裸奔 */
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4) {
  margin: 0.8em 0 0.4em;
  font-weight: 600;
  line-height: 1.4;
  color: var(--am-text);
}
.md-body :deep(h1) {
  font-size: 1.15em;
}
.md-body :deep(h2) {
  font-size: 1.08em;
}
.md-body :deep(h3),
.md-body :deep(h4) {
  font-size: 1em;
}
.md-body :deep(table) {
  border-collapse: collapse;
  margin: 0.6em 0;
  font-size: 0.92em;
  display: block; /* 窄气泡内可横滑，不撑破气泡宽度 */
  overflow-x: auto;
}
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid var(--am-line);
  padding: 5px 10px;
  text-align: left;
  white-space: nowrap;
}
.md-body :deep(th) {
  background: var(--am-blue-50);
  font-weight: 600;
}
.md-body :deep(blockquote) {
  margin: 0.6em 0;
  padding: 4px 12px;
  border-left: 3px solid var(--am-blue-200);
  color: var(--am-text-2);
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
  transition: border-color 0.16s ease, transform 0.16s ease, box-shadow 0.16s ease;
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

/* ============ 移动端（<768px，P1-3 配套）：触控目标放大，桌面密度不变 ============ */
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
  .header-right .persona-select {
    width: 104px !important;
  }
  .header-right .muted-text {
    display: none; /* 窄屏收起提示文案，给操作按钮让位 */
  }
}
</style>
