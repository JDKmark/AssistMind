import request from './request'

export function listDocs() {
  return request.get('/knowledge/list')
}

export function deleteDoc(docId) {
  return request.post('/knowledge/delete', { doc_id: docId })
}

export function rebuildIndex() {
  return request.post('/knowledge/rebuild')
}
