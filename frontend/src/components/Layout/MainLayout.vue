<template>
  <el-container class="main-layout">
    <!-- 响应式侧栏：≥1024 完整展开 / 768-1023 折叠图标栏 / <768 固定定位抽屉（汉堡按钮开合） -->
    <el-aside :width="sidebarWidth" class="sidebar" :class="{ 'is-collapsed': collapsed, 'is-open': navDrawerOpen }">
      <div class="brand">
        <div class="brand-mark">
          <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="10" cy="22" r="3" fill="#fff" />
            <circle cx="22" cy="22" r="3" fill="#fff" />
            <circle cx="16" cy="9" r="3" fill="#5b84e6" />
            <path
              d="M16 12 L11 19.5 M16 12 L21 19.5"
              stroke="#fff"
              stroke-width="1.8"
              stroke-linecap="round"
            />
          </svg>
        </div>
        <div v-if="!collapsed" class="brand-text">
          <span class="brand-name">AssistMind</span>
          <span class="brand-sub">智能客服工作台</span>
        </div>
      </div>

      <div v-if="!collapsed" class="nav-caption">工作台</div>
      <el-menu
        :default-active="route.path"
        router
        :collapse="collapsed"
        :collapse-transition="false"
      >
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>智能问答</span>
        </el-menu-item>
        <el-menu-item v-if="auth.role === 'user'" index="/orders">
          <el-icon><Goods /></el-icon>
          <span>我的订单</span>
        </el-menu-item>
        <el-menu-item v-if="canAccess('knowledge')" index="/knowledge">
          <el-icon><Document /></el-icon>
          <span>知识库</span>
        </el-menu-item>
        <el-badge
          :value="ticketUnread"
          :hidden="ticketUnread === 0"
          :max="99"
          class="ticket-badge"
        >
          <el-menu-item index="/tickets">
            <el-icon><Tickets /></el-icon>
            <span>工单</span>
          </el-menu-item>
        </el-badge>
        <el-menu-item v-if="canAccess('admin')" index="/admin">
          <el-icon><Setting /></el-icon>
          <span>管理后台</span>
        </el-menu-item>
      </el-menu>

      <div v-if="!collapsed" class="sidebar-foot">
        <span class="foot-dot"></span>
        <span class="am-mono">RAG · MCP · LangGraph</span>
      </div>
    </el-aside>
    <!-- 移动端抽屉遮罩 -->
    <div
      v-if="isMobile && navDrawerOpen"
      class="nav-backdrop"
      @click="navDrawerOpen = false"
    ></div>
    <el-container class="body-wrap">
      <el-header class="header">
        <div class="header-left">
          <!-- 移动端汉堡按钮：开合侧栏抽屉 -->
          <button
            v-if="isMobile"
            class="nav-toggle"
            aria-label="打开导航菜单"
            @click="navDrawerOpen = true"
          >
            <el-icon><Expand /></el-icon>
          </button>
          <span class="title-tick"></span>
          <span class="page-path">工作台</span>
          <span class="page-path-sep">/</span>
          <span class="page-title">{{ route.meta.title || '' }}</span>
        </div>
        <el-dropdown @command="handleCommand">
          <span class="user-info">
            <span class="user-avatar">{{ avatarChar }}</span>
            <span class="user-name">{{ auth.user?.username || 'guest' }}</span>
            <el-tag size="small" :type="roleTagType(auth.role)" effect="plain" round>
              {{ roleText }}
            </el-tag>
            <el-icon class="user-caret"><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="content am-grid-bg">
        <transition name="am-fade" mode="out-in">
          <router-view v-slot="{ Component }">
            <!-- 缓存 Chat 实例：跳转工单/订单等页面再返回时保留对话现场（消息/会话 ID/历史抽屉状态）
                 其他页面不缓存（进入即重新挂载，数据从接口拉取） -->
            <keep-alive include="Chat">
              <component :is="Component" />
            </keep-alive>
          </router-view>
        </transition>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { computed, onMounted, ref, watch } from 'vue'
import { useBreakpoint } from '@/utils/useBreakpoint'
import { roleLabel, roleTagType } from '@/utils/labels'
import {
  ticketUnread,
  startTicketPolling,
  stopTicketPolling,
  resetTicketPolling,
} from '@/utils/ticketPolling'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// ---------- 响应式侧栏（P0-1） ----------
const { isTablet, isMobile } = useBreakpoint()
// 平板（768-1023）：折叠为 64px 图标栏；手机（<768）：抽屉式
const collapsed = computed(() => isTablet.value && !isMobile.value)
const navDrawerOpen = ref(false)
const sidebarWidth = computed(() => (collapsed.value ? '64px' : '232px'))

// 工单更新轮询：登录态开启（失败静默降级，见 ticketPolling.js）
onMounted(() => {
  if (auth.token) startTicketPolling()
})

watch(
  () => auth.token,
  (token) => {
    if (token) startTicketPolling()
    else stopTicketPolling()
  },
)

// 进入工单页即清除徽标并重置轮询基线（immediate 覆盖刷新直达 /tickets 的情况）；
// 路由切换同时收起移动端导航抽屉
watch(
  () => route.path,
  (path) => {
    if (path === '/tickets') resetTicketPolling()
    navDrawerOpen.value = false
  },
  { immediate: true },
)

const roleText = computed(() => roleLabel(auth.role))

const avatarChar = computed(() =>
  (auth.user?.username || 'guest').charAt(0).toUpperCase(),
)

function canAccess(name) {
  // 路由名为大写（Admin/Knowledge），调用处传小写——大小写不敏感匹配，
  // 避免找不到路由时走「无权限限制」兜底导致越权菜单泄漏
  const r = router
    .getRoutes()
    .find((x) => String(x.name || '').toLowerCase() === String(name).toLowerCase())
  if (!r?.meta?.roles) return true
  return r.meta.roles.includes(auth.role)
}

function handleCommand(cmd) {
  if (cmd === 'logout') {
    auth.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.main-layout {
  height: 100%;
}

/* ---------- 侧边栏：深墨蓝 ---------- */
.sidebar {
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, var(--am-ink-900) 0%, var(--am-ink-950) 100%);
  overflow: hidden;
  transition: width 0.2s ease;
}

/* 折叠态（768-1023px）：图标栏 */
.sidebar.is-collapsed :deep(.el-menu-item) {
  margin: 3px 8px;
  padding-left: 0;
  padding-right: 0;
  display: flex;
  justify-content: center;
}
.sidebar.is-collapsed :deep(.el-menu-item)::before {
  display: none;
}
.sidebar.is-collapsed .brand {
  justify-content: center;
  padding-left: 0;
  padding-right: 0;
}

/* 移动端（<768px）：侧栏脱离文档流，off-canvas 抽屉 */
@media (max-width: 767px) {
  .sidebar {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    z-index: 1000;
    transform: translateX(-100%);
    visibility: hidden;
    transition: transform 0.22s ease, visibility 0s linear 0.22s;
  }
  .sidebar.is-open {
    transform: translateX(0);
    visibility: visible;
    transition: transform 0.22s ease, visibility 0s;
    box-shadow: 0 0 40px rgba(8, 20, 38, 0.35);
  }
  .user-name,
  .user-info .el-tag {
    display: none;
  }
}
.nav-backdrop {
  position: fixed;
  inset: 0;
  z-index: 999;
  background: rgba(8, 20, 38, 0.45);
  border: none;
  padding: 0;
}
.nav-toggle {
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  color: var(--am-text-2);
  font-size: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.18s ease;
}
.nav-toggle:hover {
  background: var(--am-hover-blue);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.brand-mark {
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--am-blue-600), var(--am-blue-400));
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 12px rgba(35, 82, 197, 0.35);
}
.brand-mark svg {
  width: 24px;
  height: 24px;
}
.brand-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.brand-name {
  font-size: 15px;
  font-weight: 700;
  color: #fff;
  letter-spacing: 0.01em;
}
.brand-sub {
  font-size: 11px;
  color: #7d8fb3;
  letter-spacing: 0.06em;
}

.nav-caption {
  padding: 18px 24px 8px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.14em;
  color: #8fa0c2; /* 深墨蓝底上 ≥ 4.5:1（P1-1） */
}

.sidebar :deep(.el-menu) {
  background: transparent;
  border-right: none;
  --el-menu-base-level-padding: 16px;
  --el-menu-hover-bg-color: transparent;
}
.sidebar :deep(.el-menu-item) {
  position: relative;
  height: 42px;
  line-height: 42px;
  margin: 3px 12px;
  padding-right: 12px;
  padding-left: 14px;
  border-radius: 8px;
  color: #9db0d4;
  font-size: 14px;
  transition: background-color 0.18s ease, color 0.18s ease;
}
/* 挂角指示条：hover 时左侧浮现蓝色细条（Linear 式导航反馈） */
.sidebar :deep(.el-menu-item)::before {
  content: '';
  position: absolute;
  left: -12px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 18px;
  border-radius: 2px;
  background: var(--am-blue-400);
  opacity: 0;
  transition: opacity 0.18s ease;
}
.sidebar :deep(.el-menu-item:hover)::before {
  opacity: 1;
  background: var(--am-blue-300, var(--am-blue-400));
}
.sidebar :deep(.el-menu-item.is-active)::before {
  opacity: 0;
}
.sidebar :deep(.el-menu-item:hover) {
  background: rgba(255, 255, 255, 0.06);
  color: #fff;
}
.sidebar :deep(.el-menu-item.is-active) {
  background: linear-gradient(135deg, var(--am-blue-600), var(--am-blue-500));
  color: #fff;
  box-shadow: 0 4px 14px rgba(35, 82, 197, 0.25); /* 辉光减弱，与全局克制基调统一（P2-2） */
}
.sidebar :deep(.el-menu-item .el-icon) {
  color: inherit;
}

/* el-badge 包裹菜单项：恢复块级布局，避免 inline-flex 破坏菜单项间距 */
.ticket-badge {
  display: block;
}

.sidebar-foot {
  margin-top: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 18px 24px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  font-size: 11px;
  color: #56688e;
}
.foot-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--am-blue-400);
  box-shadow: 0 0 0 3px rgba(91, 132, 230, 0.2);
}

/* ---------- 顶栏 ---------- */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  padding: 0 24px;
  border-bottom: 1px solid var(--am-line);
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(8px);
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.title-tick {
  width: 3px;
  height: 16px;
  border-radius: 2px;
  background: var(--am-blue-600);
}
.page-path {
  font-size: 13px;
  color: var(--am-text-3);
}
.page-path-sep {
  font-size: 12px;
  color: var(--am-text-3); /* 边框色当文字色几乎不可见，改用文本三级色（P2-2） */
}
.page-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--am-text);
}
.user-info {
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
}
.user-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--am-ink-800);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}
.user-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--am-text-2);
}
.user-caret {
  font-size: 12px;
  color: var(--am-text-3);
}

/* ---------- 内容区 ---------- */
.body-wrap {
  min-width: 0;
}
.content {
  padding: 0;
  background-color: var(--am-paper);
  overflow-y: auto;
}
</style>
