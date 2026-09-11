import request from './request'

/**
 * 入队评估运行任务（RQ 异步，分钟级子进程：LLM + embedding）。
 * 返回 { job_id, status }；状态经 GET /api/v1/jobs/{job_id} 轮询
 * （复用 api/knowledge.js 的 getJobStatus）。
 */
export function triggerEvaluation() {
  return request.post('/knowledge/evaluate')
}
