// 工单更新轮询（登录态 30s 一轮，驱动侧边栏"工单"菜单徽标）。
//
// 设计约束（spec: phase14 工单更新轮询）：
// - 任何失败（网络/4xx/5xx）只 console.warn，不弹窗、不抛未捕获异常，下轮自动重试
// - 徽标数字 = 用户"未看过"的回复总数：每轮以 seenSince（已读基线）查询增量，
//   基线只在两处推进——登录/页面加载时初始化为当前时间、进入 /tickets 时重置。
//   这保证徽标持续显示直到用户真正看过工单页，而不是一个轮询周期后闪没。
import { ref } from 'vue'
import { getTicketUpdates } from '@/api/ticket'

const POLL_INTERVAL_MS = 30000

// 徽标数字（MainLayout 直接消费）
export const ticketUnread = ref(0)

// 已读基线（naive UTC ISO；空串 = 尚未初始化，首轮查询返回空仅建立基线语义由 start 显式初始化替代）
const seenSince = ref('')

let timer = null

function nowIso() {
  // 客户端本地时钟的 UTC ISO；服务端解析 Z 后缀后按 naive UTC 比较
  return new Date().toISOString()
}

async function pollOnce() {
  try {
    const data = await getTicketUpdates(seenSince.value || null)
    const tickets = Array.isArray(data?.tickets) ? data.tickets : []
    let unread = 0
    for (const t of tickets) unread += t.new_replies || 0
    ticketUnread.value = unread
  } catch (e) {
    // 静默降级：不弹窗、不动基线，下一轮询周期自动重试
    console.warn('[ticketPolling] 轮询失败，将于下轮自动重试:', e?.message || e)
  }
}

export function startTicketPolling() {
  if (timer) return // 已在轮询中（token watch 可能重复触发），防多定时器
  if (!seenSince.value) seenSince.value = nowIso() // 登录基线：只提示登录之后发生的新回复
  pollOnce() // 立即先跑一轮，不必等 30s
  timer = setInterval(pollOnce, POLL_INTERVAL_MS)
}

export function stopTicketPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
  // 登出即清徽标（下次登录 startTicketPolling 重新建立基线）
  ticketUnread.value = 0
  seenSince.value = ''
}

export function resetTicketPolling() {
  // 进入 /tickets：页面自身展示最新工单，徽标清零并把已读基线推进到当前时间
  ticketUnread.value = 0
  seenSince.value = nowIso()
}
