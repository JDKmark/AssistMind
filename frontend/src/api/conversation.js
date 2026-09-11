import request from './request'

// 会话列表：GET /api/v1/conversations?limit=&offset=
// 返回 {conversations: [{id, title, created_at, updated_at, message_count}], total}
// （仅本人会话，updated_at 倒序）
export function listConversations(limit = 20, offset = 0) {
  return request.get('/conversations', { params: { limit, offset } })
}

// 会话历史消息：GET /api/v1/conversations/{id}/messages
// 返回 {conversation_id, messages: [{role: 'user'|'assistant', content, intent, created_at}]}
// （按时间正序；他人会话或不存在统一 404 防枚举）
export function listMessages(conversationId) {
  return request.get(`/conversations/${conversationId}/messages`)
}
