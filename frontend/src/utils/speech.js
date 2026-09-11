// 语音 AI 封装：ASR 浏览器识别 + TTS 两级降级播报
//
// 设计约束：
// - ASR：浏览器 Web Speech API（零延迟、零后端依赖；Chrome/Edge 覆盖演示场景）
// - TTS 两级降级：
//   1. 后端 edge-tts 云端神经音色（POST /api/v1/tts/speak，免费无 key，
//      晓晓等音色远优于 Windows 系统合成）
//   2. 后端不可用（网络/503）→ console.warn 后回落浏览器 speechSynthesis
//   两者都不可用才返回 false（调用方隐藏入口），不静默失败
// - ASR 与 TTS 互斥：开始识别前先 stopSpeaking()，避免麦克风收到播报自循环
import { synthesizeSpeech } from '@/api/tts'

const LANG_ZH = 'zh-CN'

export function isSpeechRecognitionSupported() {
  return (
    typeof window !== 'undefined' &&
    Boolean(window.SpeechRecognition || window.webkitSpeechRecognition)
  )
}

export function isSpeechSynthesisSupported() {
  return typeof window !== 'undefined' && Boolean(window.speechSynthesis)
}

/** 后端 TTS 可用性：需要 Audio 元素与 fetch（老浏览器/无后端环境直接走浏览器合成）。 */
function isBackendTtsAvailable() {
  return (
    typeof window !== 'undefined' &&
    typeof window.Audio === 'function' &&
    typeof window.fetch === 'function'
  )
}

// 模块级单例：同时只播一条，新播报打断旧播报（见 speak/stopSpeaking）
let audioEl = null
let audioUrl = null
// 播报代际号：stopSpeaking()（含 speak 内部打断）递增。stopSpeaking 只能停
// 已创建的 audioEl，管不到还在途的后端合成 fetch——旧合成返回后据此自我废弃，
// 否则会出现两条音频同时出声 + objectURL 覆盖泄漏。
let speakSeq = 0

function releaseAudio() {
  if (audioEl) {
    try {
      audioEl.pause()
    } catch (e) {
      // 已失效的音频元素，忽略
    }
    audioEl = null
  }
  if (audioUrl) {
    try {
      URL.revokeObjectURL(audioUrl)
    } catch (e) {
      // objectURL 已失效，忽略
    }
    audioUrl = null
  }
}

/**
 * 创建语音识别器（单次识别模式）。
 * 返回 null 表示当前浏览器不支持（调用方据此隐藏麦克风入口）。
 */
export function createRecognizer({ lang = LANG_ZH, onResult, onEnd, onError } = {}) {
  if (!isSpeechRecognitionSupported()) return null
  const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition
  const recognizer = new Ctor()
  recognizer.lang = lang
  recognizer.interimResults = false // 单次识别最终结果，无需中间态闪烁
  recognizer.continuous = false

  recognizer.onresult = (event) => {
    const text = Array.from(event.results)
      .map((r) => String(r[0].transcript || '').trim())
      .filter(Boolean)
      .join(' ')
      .trim()
    if (text && onResult) onResult(text)
  }
  recognizer.onerror = (event) => {
    if (onError) onError(event?.error || '识别失败')
  }
  recognizer.onend = () => {
    if (onEnd) onEnd()
  }
  return recognizer
}

function pickChineseVoice() {
  const voices = window.speechSynthesis.getVoices()
  return voices.find((v) => v.lang && v.lang.toLowerCase().startsWith('zh')) || null
}

function speakViaBrowser(text, lang) {
  window.speechSynthesis.cancel()
  const utter = new window.SpeechSynthesisUtterance(text)
  utter.lang = lang
  const voice = pickChineseVoice()
  if (voice) utter.voice = voice
  window.speechSynthesis.speak(utter)
}

async function speakViaBackend(text, lang, seq) {
  let url = null
  try {
    url = await synthesizeSpeech(text)
    if (seq !== speakSeq) {
      // 合成期间播报已被打断/停止：丢弃本次音频（revoke 防泄漏），不发声
      try {
        URL.revokeObjectURL(url)
      } catch (e) {
        // objectURL 已失效，忽略
      }
      return
    }
    audioUrl = url
    audioEl = new window.Audio(url)
    audioEl.onended = () => releaseAudio() // 播完释放 objectURL 防泄漏
    audioEl.onerror = () => releaseAudio()
    await audioEl.play()
  } catch (e) {
    console.warn('[speech] 云端音色播报失败，回落浏览器合成：', e?.message || e)
    if (seq !== speakSeq) return // 已被打断：不再回落发声
    releaseAudio()
    if (isSpeechSynthesisSupported()) speakViaBrowser(text, lang)
  }
}

/**
 * 语音播报（两级降级）。新播报自动打断旧播报；完全不可用时返回 false。
 * 注意：后端路径异步执行（不等合成完成），同步返回值只表示「存在可行播报路径」。
 */
export function speak(text, { lang = LANG_ZH } = {}) {
  if (!text) return false
  const canBackend = isBackendTtsAvailable()
  const canBrowser = isSpeechSynthesisSupported()
  if (!canBackend && !canBrowser) return false
  stopSpeaking() // 新播报打断旧播报（含作废其在途合成）
  speakSeq += 1
  const seq = speakSeq
  if (canBackend) {
    void speakViaBackend(text, lang, seq) // fire-and-forget：失败内部回落浏览器
  } else {
    speakViaBrowser(text, lang)
  }
  return true
}

/** 停止播报（可中断，幂等）：停云端音频 + 作废在途合成 + 停浏览器合成。 */
export function stopSpeaking() {
  speakSeq += 1
  releaseAudio()
  if (isSpeechSynthesisSupported()) window.speechSynthesis.cancel()
}
