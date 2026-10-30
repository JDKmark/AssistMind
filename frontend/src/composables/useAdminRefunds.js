import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listRefunds, updateRefundStatus } from '@/api/mall'

/**
 * 退款处理：列表加载与状态流转（通过 / 拒绝）。
 *
 * 从 views/Admin/index.vue 抽出。流转成功后要同时刷新退款列表与统一概览，
 * 概览数据归 useAdminOverview 所有，故由调用方注入刷新函数。
 *
 * @param {object} deps
 * @param {() => Promise<void>} deps.reloadAdminData 用户/概览/审计刷新
 */
export function useAdminRefunds({ reloadAdminData }) {
  const refunds = ref([])
  const refundTotal = ref(0)

  async function loadRefunds() {
    try {
      const data = await listRefunds({ limit: 20, offset: 0 })
      refunds.value = data.refunds || []
      refundTotal.value = data.total || 0
    } catch (e) {
      // 错误提示已由 request 拦截器统一处理；与兄弟加载函数保持一致兜底，
      // 避免后端宕机时产生 unhandled rejection
      console.warn('[Admin] 退款列表加载失败', e)
    }
  }

  async function changeRefundStatus(refund, status) {
    try {
      await updateRefundStatus(refund.refund_id, status)
      ElMessage.success(`退款单 ${refund.refund_id} 已置为「${status}」`)
    } catch (e) {
      console.warn('[Admin] 退款流转失败', e)
      return
    }
    await Promise.all([loadRefunds(), reloadAdminData()])
  }

  return { refunds, refundTotal, loadRefunds, changeRefundStatus }
}
