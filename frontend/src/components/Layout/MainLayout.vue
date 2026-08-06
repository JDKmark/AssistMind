<template>
  <el-container class="main-layout">
    <el-aside width="220px" class="sidebar">
      <div class="logo">AssistMind</div>
      <el-menu :default-active="route.path" router>
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>智能问答</span>
        </el-menu-item>
        <el-menu-item index="/ops">
          <el-icon><Monitor /></el-icon>
          <span>运维诊断</span>
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
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="page-title">{{ route.meta.title || '' }}</span>
        <el-dropdown @command="handleCommand">
          <span class="user-info">
            {{ auth.user?.username || 'guest' }}
            <el-tag size="small" :type="roleTagType">{{ auth.role }}</el-tag>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="content">
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

function canAccess(name) {
  const r = router.getRoutes().find((x) => x.name === name)
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
.sidebar {
  background: #001529;
  color: #fff;
}
.logo {
  height: 60px;
  line-height: 60px;
  text-align: center;
  font-size: 20px;
  font-weight: bold;
  color: #fff;
  border-bottom: 1px solid #1f1f1f;
}
.sidebar :deep(.el-menu) {
  background: transparent;
  border-right: none;
}
.sidebar :deep(.el-menu-item) {
  color: #b7b7b7;
}
.sidebar :deep(.el-menu-item.is-active) {
  color: #fff;
  background: #1890ff;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #eee;
  background: #fff;
}
.page-title {
  font-size: 18px;
  font-weight: 500;
}
.user-info {
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
}
.content {
  background: #f5f5f5;
}
</style>
