import { useAuthStore } from '@/stores/auth'

// 云端语音合成：POST /api/v1/tts/speak（JWT 鉴权）→ audio/mpeg 流。
// 二进制响应不走 axios 封装（request.js 只处理 JSON），与 chat.js 同款用原生 fetch；
// 收完整流 → blob → objectURL 交给调用方 new Audio() 播放。
// 失败抛错（含后端 detail），调用方（speech.js）据此回落浏览器 speechSynthesis。
export async function synthesizeSpeech(text) {
  const auth = useAuthStore()

  let response
  try {
    response = await fetch('/api/v1/tts/speak', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${auth.token}`,
      },
      body: JSON.stringify({ text }),
    })
  } catch (e) {
    throw new Error(e.message || '语音合成请求失败')
  }

  if (!response.ok) {
    let message = `语音合成失败（${response.status}）`
    try {
      const data = await response.json()
      if (data && data.detail) message = data.detail
    } catch (e) {
      // 非 JSON 错误响应体，保留状态码消息
    }
    throw new Error(message)
  }

  const blob = await response.blob()
  if (!blob || blob.size === 0) throw new Error('语音合成返回空音频')
  return URL.createObjectURL(blob)
}
