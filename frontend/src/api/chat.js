import { useAuthStore } from '@/stores/auth'

// SSE 流式聊天：不能用 axios，用原生 fetch + ReadableStream 逐帧解析
// POST /api/v1/chat/ask，body {query, history}
// 后端事件（backend/app/api/chat.py）：
//   start {query, intent} / retrieving {} / rewriting {variants} /
//   generating {} / tool_call {tool_name, arguments} /
//   tool_result {tool_name, result} / done {answer, sources?} / error {message}
// 所有事件都会回调 onEvent(eventName, dataObj)；done 后回调 onDone(dataObj)；
// 出错时回调 onError(message)，且仅回调一次。
export async function chatStream(query, { history = [], signal, onEvent, onDone, onError } = {}) {
  const auth = useAuthStore()
  let reported = false
  const reportError = (message) => {
    if (reported) return
    reported = true
    onError(message)
  }

  let response
  try {
    response = await fetch('/api/v1/chat/ask', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${auth.token}`,
      },
      body: JSON.stringify({ query, history }),
      signal,
    })
  } catch (e) {
    reportError(e.name === 'AbortError' ? '发送已取消' : (e.message || '网络请求失败'))
    return
  }

  if (!response.ok) {
    let message = `请求失败（${response.status}）`
    try {
      const data = await response.json()
      if (data && data.detail) message = data.detail
    } catch (e) {
      // 非 JSON 错误响应体，保留状态码消息
    }
    reportError(message)
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  // 解析单帧：`event: <名称>\ndata: <json>`（兼容多行 data 与空闲行）
  const handleFrame = (frame) => {
    let eventName = ''
    const dataLines = []
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim())
      }
    }
    if (!eventName) return false
    let dataObj = {}
    if (dataLines.length > 0) {
      try {
        dataObj = JSON.parse(dataLines.join('\n'))
      } catch (e) {
        dataObj = {}
      }
    }
    onEvent(eventName, dataObj)
    if (eventName === 'done') {
      onDone(dataObj)
    } else if (eventName === 'error') {
      reportError(dataObj.message || '服务异常，请稍后重试')
      return true
    }
    return false
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sepIdx
      // 兼容 \n\n 与 \r\n\r\n 帧分隔，残段保留在 buffer 中等待后续 chunk
      while ((sepIdx = buffer.search(/\n\n|\r\n\r\n/)) !== -1) {
        const raw = buffer.slice(0, sepIdx)
        buffer = buffer.slice(sepIdx + (buffer[sepIdx] === '\r' ? 4 : 2))
        if (handleFrame(raw.replace(/\r\n/g, '\n'))) return
      }
    }
    if (buffer.trim()) handleFrame(buffer.replace(/\r\n/g, '\n'))
  } catch (e) {
    if (e.name === 'AbortError') {
      reportError('发送已取消')
    } else if (!reported) {
      reportError(e.message || '对话流读取异常')
    } else {
      // onError 回调抛出的异常（调用方主动抛出），原样传播给调用方
      throw e
    }
  }
}
