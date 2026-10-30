import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { triggerEvaluation } from '@/api/evaluate'
import { getJobStatus } from '@/api/knowledge'

const EVAL_POLL_INTERVAL = 5000
const EVAL_POLL_MAX = 60 // 5 分钟封顶：评估为分钟级子进程，超时后提示稍后自查

/**
 * 运行评估：RQ 任务入队 + 封顶轮询。
 *
 * 从 views/Admin/index.vue 抽出。轮询必须可取消——组件卸载后不再空转 5 分钟，
 * 也不在其他页面弹完成/失败提示（dispose 由页面 onUnmounted 调用）。
 */
export function useAdminEval() {
  const evalRunning = ref(false)
  const evalStatusText = ref('')

  // 组件卸载置位：进行中的评估任务轮询停止
  let disposed = false
  let pollTimer = null
  let pollResolve = null

  /**
   * 可取消的等待。
   *
   * 直接 `await new Promise(r => setTimeout(r, ms))` 在卸载后仍会挂满一个
   * 轮询周期（5s）才走下一轮判断；这里保留 resolve 引用，卸载时立即唤醒，
   * 循环随即命中 disposed 退出，不留空转。
   */
  function pollSleep(ms) {
    return new Promise((resolve) => {
      pollResolve = resolve
      pollTimer = setTimeout(() => {
        pollTimer = null
        pollResolve = null
        resolve()
      }, ms)
    })
  }

  async function pollEvalJob(jobId) {
    // 组件卸载后停止轮询（disposed 由 dispose 置位）：
    // 不再空转 5 分钟，也不在其他页面弹完成/失败提示
    for (let i = 0; i < EVAL_POLL_MAX; i++) {
      await pollSleep(EVAL_POLL_INTERVAL)
      if (disposed) return
      try {
        const job = await getJobStatus(jobId)
        if (disposed) return // 查询挂起期间组件已卸载：不弹完成/失败提示，不写状态
        if (job.status === 'finished') {
          if (job.result?.ok) {
            evalStatusText.value = '评估完成'
            ElMessage.success('评估完成，结果已写入评估集目录')
          } else {
            evalStatusText.value = '评估失败'
            ElMessage.error('评估运行失败，请查看 worker 日志')
          }
          return
        }
        if (job.status === 'failed') {
          evalStatusText.value = '评估失败'
          ElMessage.error(`评估失败：${job.error || '未知错误'}`)
          return
        }
        evalStatusText.value = `评估运行中…（已等待 ${((i + 1) * EVAL_POLL_INTERVAL) / 1000}s）`
      } catch (e) {
        console.warn('[Admin] 评估状态查询失败', e)
      }
    }
    evalStatusText.value = '评估仍在后台执行，稍后刷新查看结果'
  }

  async function runEvaluation() {
    if (evalRunning.value) return
    evalRunning.value = true
    try {
      const data = await triggerEvaluation()
      const jobId = data.job_id
      if (!jobId) throw new Error('未返回任务 ID')
      evalStatusText.value = '评估已入队，运行中…'
      await pollEvalJob(jobId)
    } catch (e) {
      console.warn('[Admin] 评估任务触发失败', e)
      evalStatusText.value = ''
    } finally {
      evalRunning.value = false
    }
  }

  // 卸载：停轮询并唤醒在途等待（否则还要挂满一个 EVAL_POLL_INTERVAL）
  function dispose() {
    disposed = true
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
    pollResolve?.()
    pollResolve = null
  }

  return { evalRunning, evalStatusText, runEvaluation, pollEvalJob, pollSleep, dispose }
}
