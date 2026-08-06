import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

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
        path: 'tickets',
        name: 'Tickets',
        component: () => import('@/views/Tickets/index.vue'),
        meta: { title: '工单' },
      },
      {
        path: 'ops',
        name: 'Ops',
        component: () => import('@/views/Ops/index.vue'),
        meta: { title: '运维诊断' },
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

// 全局前置守卫：未登录跳转 login
router.beforeEach((to, from, next) => {
  const auth = useAuthStore()
  if (to.meta.public) {
    next()
  } else if (!auth.token) {
    next('/login')
  } else {
    next()
  }
})

export default router
