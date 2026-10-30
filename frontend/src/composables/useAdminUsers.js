import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getOverview, listUsers, updateUser, listAuditLogs } from '@/api/admin'

/**
 * 用户管理 + 操作审计：列表加载、角色变更、启停。
 *
 * 从 views/Admin/index.vue 抽出。变更后要刷新「用户 + 概览 + 审计」三块，
 * 其中概览数据归 useAdminOverview 所有，故通过 adminOverview ref 注入，
 * 而不是让本模块再发一次 getOverview 另存一份。
 *
 * @param {object} deps
 * @param {import('vue').Ref<object>} deps.adminOverview 统一概览数据（变更后刷新）
 */
export function useAdminUsers({ adminOverview }) {
  const users = ref([])
  const userTotal = ref(0)
  const auditLogs = ref([])
  const auditTotal = ref(0)

  async function loadAdminData(includeOverview = true) {
    // 后端不可用时降级为空数据（拦截器节流提示一次，页面横幅提示重试），不裸抛
    try {
      const requests = [listUsers(), listAuditLogs()]
      if (includeOverview) requests.unshift(getOverview())
      const data = await Promise.all(requests)
      const overview = includeOverview ? data[0] : null
      const userData = includeOverview ? data[1] : data[0]
      const auditData = includeOverview ? data[2] : data[1]
      if (overview) adminOverview.value = overview
      users.value = userData.users || []
      userTotal.value = userData.total || 0
      auditLogs.value = auditData.items || []
      auditTotal.value = auditData.total || 0
    } catch (e) {
      console.warn('[Admin] 用户/审计数据加载失败', e)
      users.value = []
      auditLogs.value = []
    }
  }

  async function changeUserRole(user, role) {
    try {
      await updateUser(user.id, { role })
      ElMessage.success(`已将 ${user.username} 设为${role === 'agent' ? '客服' : '用户'}`)
    } catch (e) {
      console.warn('[Admin] 角色变更失败', e)
      return
    }
    await loadAdminData()
  }

  async function toggleUser(user) {
    try {
      await updateUser(user.id, { is_active: !user.is_active })
      ElMessage.success(user.is_active ? `已停用 ${user.username}` : `已启用 ${user.username}`)
    } catch (e) {
      console.warn('[Admin] 启停失败', e)
      return
    }
    await loadAdminData()
  }

  return {
    users,
    userTotal,
    auditLogs,
    auditTotal,
    loadAdminData,
    changeUserRole,
    toggleUser,
  }
}
