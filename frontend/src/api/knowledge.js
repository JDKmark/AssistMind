import request from './request'

export function listDocs() {
  return request.get('/knowledge/list')
}
