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

// 上传文档入队（multipart/form-data：file + 可选 category）：返回 {job_id, status}
export function uploadDoc(formData) {
  return request.post('/knowledge/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// 文档 chunk 明细（doc_id 可含 `/`，如 uploads/退货规则，路径直接拼接）
export function getDocChunks(docId) {
  return request.get(`/knowledge/${docId}/chunks`)
}

// 召回测试 dry-run：{query, top_k} → {query, vector, bm25, fused, degraded?}
export function searchTest(payload) {
  return request.post('/knowledge/search-test', payload)
}
