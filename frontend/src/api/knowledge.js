import request from './request'

export function listDocs() {
  return request.get('/knowledge/list')
}

export function deleteDoc(docId) {
  return request.post('/knowledge/delete', { doc_id: docId })
}

// 重建为 RQ 异步任务：返回 {job_id, status}，耗时执行在 worker 进程
export function rebuildIndex() {
  return request.post('/knowledge/rebuild')
}

// 轮询任务状态：{job_id, status: queued/started/finished/failed, result?, error?}
export function getJobStatus(jobId) {
  return request.get(`/jobs/${jobId}`)
}
