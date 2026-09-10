<template>
  <el-container class="main-layout">
    <el-aside width="232px" class="sidebar">
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
        <div class="brand-text">
          <span class="brand-name">AssistMind</span>
          <span class="brand-sub">智能客服工作台</span>
        </div>
      </div>

      <div class="nav-caption">工作台</div>
      <el-menu :default-active="route.path" router>
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
        <el-menu-item index="/tickets">
          <el-icon><Tickets /></el-icon>
          <span>工单</span>
        </el-menu-item>
        <el-menu-item v-if="canAccess('admin')" index="/admin">
          <el-icon><Setting /></el-icon>
          <span>管理后台</span>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-foot">
        <span class="foot-dot"></span>
        <span class="am-mono">RAG · MCP · LangGraph</span>
      </div>
    </el-aside>
    <el-container class="body-wrap">
      <el-header class="header">
        <div class="header-left">
          <span class="title-tick"></span>
          <span class="page-title">{{ route.meta.title || '' }}</span>
        </div>
        <el-dropdown @command="handleCommand">
          <span class="user-info">
            <span class="user-avatar">{{ avatarChar }}</span>
            <span class="user-name">{{ auth.user?.username || 'guest' }}</span>
            <el-tag size="small" :type="roleTagType" effect="plain" round>
              {{ auth.role }}
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
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { computed } from 'vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const roleTagType = computed(() => {
  const map = { admin: 'danger', agent: 'warning', user: 'info' }
  return map[auth.role] || 'info'
})

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
  color: #5a6c92;
}

.sidebar :deep(.el-menu) {
  background: transparent;
  border-right: none;
  --el-menu-base-level-padding: 16px;
  --el-menu-hover-bg-color: transparent;
}
.sidebar :deep(.el-menu-item) {
  height: 42px;
  line-height: 42px;
  margin: 3px 12px;
  padding-right: 12px;
  border-radius: 8px;
  color: #9db0d4;
  font-size: 14px;
  transition: background-color 0.18s ease, color 0.18s ease;
}
.sidebar :deep(.el-menu-item:hover) {
  background: rgba(255, 255, 255, 0.06);
  color: #fff;
}
.sidebar :deep(.el-menu-item.is-active) {
  background: linear-gradient(135deg, var(--am-blue-600), var(--am-blue-500));
  color: #fff;
  box-shadow: 0 4px 14px rgba(35, 82, 197, 0.4);
}
.sidebar :deep(.el-menu-item .el-icon) {
  color: inherit;
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
