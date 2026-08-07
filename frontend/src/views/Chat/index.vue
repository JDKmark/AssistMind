<template>
  <div class="chat-page">
    <el-card shadow="never" class="panel-card chat-panel">
      <template #header>
        <div class="card-header">
          <span class="card-title">智能客服</span>
          <span class="muted-text">可咨询商品、订单、物流与售后问题</span>
        </div>
      </template>

      <!-- 消息列表 -->
      <div ref="listRef" class="chat-messages">
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

              <!-- 查询改写 -->
              <div v-if="m.rewrites && m.rewrites.length" class="rewrite-line">
                <el-tag size="small" type="info" effect="plain">查询已改写</el-tag>
                <span class="muted-text">已根据您的描述扩展检索</span>
              </div>

              <!-- 工具调用过程（task 意图） -->
              <div v-if="m.toolCalls.length" class="tool-section">
                <div
                  v-for="(tc, i) in m.toolCalls"
                  :key="i"
                  class="tool-item"
                >
                  <el-tag size="small" type="warning">工具调用</el-tag>
                  <span class="tool-name">调用 {{ toolLabel(tc.tool_name) }}</span>
                  <span v-if="toolArgsText(tc)" class="tool-args">{{ toolArgsText(tc) }}</span>

                  <!-- 工具结果卡片（订单/物流/商品/售后） -->
                  <div v-if="cardOf(tc)" class="tool-result-card">
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

              <!-- 知识来源（faq 意图） -->
              <div v-if="m.sources.length" class="sources-section">
                <div class="sources-title">知识来源</div>
                <div v-for="(s, i) in m.sources" :key="i" class="source-item">
                  <span class="source-index">{{ i + 1 }}</span>
                  <span class="source-title">{{ s.title || s.snippet || '知识库命中' }}</span>
                </div>
              </div>

              <!-- 诊断摘要提示（diagnose 意图） -->
              <el-alert
                v-if="m.intent === 'diagnose' && m.status === 'done'"
                type="success"
                :closable="false"
                show-icon
                class="ticket-alert"
              >
                <template #title>
                  已生成诊断报告，可在
                  <router-link to="/ops" class="ticket-link">运维诊断页</router-link>
                  查看
                </template>
              </el-alert>

              <!-- 工单提示 -->
              <el-alert
                v-if="m.ticketId"
                type="success"
                :closable="false"
                show-icon
                class="ticket-alert"
              >
                <template #title>
                  {{ m.intent === 'diagnose' ? '已创建故障工单' : '已创建售后工单' }}
                  {{ m.ticketId }}，前往
                  <router-link to="/tickets" class="ticket-link">工单列表</router-link>
                </template>
              </el-alert>

              <!-- 回答 -->
              <div v-if="m.content" class="bubble-text md-body" v-html="renderMd(m.content)" />
              <div
                v-else-if="m.status === 'done' && !m.report"
                class="bubble-text muted-text"
              >
                （暂无回答，可尝试转人工客服）
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

      <!-- 快捷提问引导条 -->
      <div class="quick-row">
        <span class="muted-text">快捷提问：</span>
        <el-button
          v-for="qq in QUICK_QUESTIONS"
          :key="qq.text"
          link
          type="primary"
          class="quick-btn"
          @click="inputText = qq.text"
        >
          {{ qq.label }}：{{ qq.text }}
        </el-button>
      </div>

      <!-- 输入区 -->
      <div class="input-row">
        <el-input
          v-model="inputText"
          type="textarea"
          :rows="3"
          placeholder="请输入您的问题，Enter 发送，Shift+Enter 换行"
          @keydown.enter.exact.prevent="onSend"
        />
        <div class="send-actions">
          <el-button
            type="primary"
            :disabled="!canSend"
            :loading="streaming"
            @click="onSend"
          >
            发送
          </el-button>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import { chatStream } from '@/api/chat'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

// 快捷提问引导（商品咨询 / 查订单 / 我要退货 / 物流查询）
const QUICK_QUESTIONS = [
  { label: '商品咨询', text: '华为 Mate 60 Pro 多少钱' },
  { label: '查订单', text: '查一下订单 20240801001' },
  { label: '我要退货', text: '我要退货' },
  { label: '物流查询', text: '物流到哪了' },
]

// 流式阶段 → 展示文案（对齐后端 SSE 事件）
const STAGE_TEXT = {
  start: '正在思考…',
  retrieving: '正在检索知识库…',
  generating: '正在生成回答…',
  planning: '正在规划诊断…',
  collecting: '正在采集故障证据…',
  evidence: '正在分析证据…',
  analyzing: '正在分析根因…',
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

const canSend = computed(() => Boolean(inputText.value.trim()) && !streaming.value)

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
    report: null,
    ticketId: '',
    errorMsg: '',
  }
}

function stageText(m) {
  return STAGE_TEXT[m.stage] || (m.intent === 'diagnose' ? '正在分析…' : '正在思考…')
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

// 惰性缓存解析结果（模板中多次引用只算一次）
function cardOf(tc) {
  if (!tc._card) tc._card = parseToolResult(tc)
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
  return md.render(text)
}

// 处理一条 SSE 事件，更新对应客服消息
function handleEvent(msg, name, data) {
  switch (name) {
    case 'start':
      msg.intent = data.intent || ''
      msg.stage = 'start'
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
    case 'tool_call':
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
    // diagnose 意图阶段（backend ops._diagnose_stream）
    case 'planning':
      msg.stage = 'planning'
      break
    case 'collecting':
      msg.stage = 'collecting'
      break
    case 'evidence':
      msg.stage = 'evidence'
      if (Array.isArray(data.kb)) {
        msg.sources = data.kb.map((kb) => ({
          title: kb.title || kb.content || '知识库命中',
        }))
      }
      break
    case 'analyzing':
      msg.stage = 'analyzing'
      break
    case 'done':
      msg.stage = 'done'
      msg.status = 'done'
      msg.content = data.answer || ''
      if (Array.isArray(data.sources)) msg.sources = data.sources
      msg.report = data.report || null
      if (data.ticket_id) msg.ticketId = data.ticket_id
      if (msg.report && msg.report.ticket_id) msg.ticketId = msg.report.ticket_id
      // diagnose 意图：done 无 answer，用报告摘要作为回答
      if (msg.intent === 'diagnose' && !msg.content && msg.report) {
        msg.content = msg.report.summary || ''
      }
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

async function onSend() {
  const q = inputText.value.trim()
  if (!q || streaming.value) return
  const assistantMsg = newMessage('assistant', '')
  messages.value.push(newMessage('user', q), assistantMsg)
  inputText.value = ''
  streaming.value = true
  scrollToBottom()
  try {
    await chatStream(q, {
      history: history.value.slice(),
      onEvent: (name, data) => {
        handleEvent(assistantMsg, name, data)
        if (name === 'done') {
          history.value.push({ role: 'user', content: q })
          history.value.push({ role: 'assistant', content: assistantMsg.content || '' })
        }
      },
      onDone: () => {
        streaming.value = false
      },
      onError: (message) => {
        streaming.value = false
        handleError(assistantMsg, message)
      },
    })
  } catch (e) {
    // chatStream 内部已上报错误（reported 守卫），此处仅兜底清理状态
    streaming.value = false
    handleError(assistantMsg, (e && e.message) || '网络请求失败')
  }
}

function scrollToBottom() {
  nextTick(() => {
    const el = listRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

watch(messages, scrollToBottom, { deep: true })
</script>

<style scoped>
.chat-page {
  padding: 16px;
}
.panel-card {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
.card-title {
  font-weight: 600;
}
.muted-text {
  color: #909399;
  font-size: 13px;
}

/* 消息列表 */
.chat-messages {
  height: 420px;
  overflow-y: auto;
  padding: 12px;
  background: #f7f8fa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
}
.chat-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
}
.chat-row {
  display: flex;
  margin-bottom: 10px;
}
.chat-row.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 76%;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}
.bubble.user {
  background: #409eff;
  color: #fff;
  border-top-right-radius: 2px;
}
.bubble.assistant {
  background: #fff;
  color: #303133;
  border: 1px solid #ebeef5;
  border-top-left-radius: 2px;
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
  color: #909399;
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
  color: #303133;
}
.tool-args {
  color: #909399;
  font-size: 12px;
  font-family: Consolas, Menlo, monospace;
  word-break: break-all;
}

/* 工具结果卡片 */
.tool-result-card {
  margin-top: 6px;
  padding: 8px;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  width: 100%;
}
.card-desc {
  width: 100%;
}
.logistics-timeline {
  padding-left: 4px;
}
.service-tag {
  margin-right: 4px;
}

/* 知识来源 */
.sources-section {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed #e4e7ed;
}
.sources-title {
  font-weight: 600;
  color: #606266;
  font-size: 13px;
  margin-bottom: 4px;
}
.source-item {
  display: flex;
  gap: 6px;
  font-size: 13px;
  color: #606266;
  padding: 2px 0;
}
.source-index {
  color: #409eff;
  flex-shrink: 0;
}
.source-title {
  word-break: break-all;
}

/* 工单 / 诊断提示 */
.ticket-alert {
  margin: 8px 0 4px;
}
.ticket-link {
  color: #409eff;
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
  background: #f5f7fa;
  border-radius: 4px;
  padding: 8px;
  overflow-x: auto;
  font-size: 12px;
}
.md-body :deep(code) {
  background: #f5f7fa;
  border-radius: 3px;
  padding: 1px 4px;
  font-size: 12px;
}
.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}

/* 快捷提问 */
.quick-row {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  margin: 12px 0;
}
.quick-btn {
  margin: 2px 0;
}

/* 输入区 */
.input-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.input-row .el-input {
  flex: 1;
}
.send-actions {
  flex-shrink: 0;
  padding-top: 2px;
}

@media (max-width: 900px) {
  .input-row {
    flex-direction: column;
  }
  .send-actions {
    width: 100%;
    display: flex;
    justify-content: flex-end;
  }
}
</style>
