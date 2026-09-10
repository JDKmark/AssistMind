import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { hasPermission } from '@/utils/permissions'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login/index.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    component: () => import('@/components/Layout/MainLayout.vue'),
    redirect: '/chat',
    children: [
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/views/Chat/index.vue'),
        meta: { title: '智能问答' },
      },
      {
        path: 'knowledge',
        name: 'Knowledge',
        component: () => import('@/views/Knowledge/index.vue'),
        meta: { title: '知识库', roles: ['admin', 'agent'] },
      },
      {
        path: 'orders',
        name: 'Orders',
        component: () => import('@/views/Orders/index.vue'),
        meta: { title: '我的订单' },
      },
      {
        path: 'tickets',
        name: 'Tickets',
        component: () => import('@/views/Tickets/index.vue'),
        meta: { title: '工单' },
      },
      {
        path: 'admin',
        name: 'Admin',
        component: () => import('@/views/Admin/index.vue'),
        meta: { title: '管理后台', roles: ['admin'] },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/chat',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 全局前置守卫：未登录跳转 login；meta.roles 未授权（如 user 访问 Admin）跳回 chat
router.beforeEach((to, from, next) => {
  const auth = useAuthStore()
  if (to.meta.public) {
    next()
  } else if (!auth.token) {
    next('/login')
  } else if (!hasPermission(auth.role, to.meta.roles)) {
    ElMessage.warning('没有访问该页面的权限')
    next('/chat')
  } else {
    next()
  }
})

export default router
