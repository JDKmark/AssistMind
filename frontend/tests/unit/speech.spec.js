import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// ---------- 云端 TTS API mock（@/api/tts，两级降级的后端一级） ----------

const ttsApi = vi.hoisted(() => ({
  synthesizeSpeech: vi.fn(),
}))

vi.mock('@/api/tts', () => ({
  synthesizeSpeech: ttsApi.synthesizeSpeech,
}))

// ---------- Web Speech API mock ----------

class FakeRecognition {
  constructor() {
    this.lang = ''
    this.interimResults = false
    this.continuous = false
    this.onresult = null
    this.onerror = null
    this.onend = null
  }
  start() {}
  stop() {}
}

const speechApi = vi.hoisted(() => ({
  synthesis: {
    cancel: vi.fn(),
    speak: vi.fn(),
    getVoices: vi.fn(() => []),
  },
  utterances: [],
}))

vi.stubGlobal('webkitSpeechRecognition', FakeRecognition)
vi.stubGlobal('speechSynthesis', speechApi.synthesis)
vi.stubGlobal(
  'SpeechSynthesisUtterance',
  class FakeUtterance {
    constructor(text) {
      this.text = text
      this.lang = ''
      this.voice = null
      speechApi.utterances.push(this)
    }
  }
)

// ---------- 云端音频路径 mock（Audio 元素 + objectURL） ----------

const audioApi = vi.hoisted(() => ({ instances: [] }))

class FakeAudio {
  constructor(url) {
    this.src = url
    this.onended = null
    this.onerror = null
    this.play = vi.fn(() => Promise.resolve())
    this.pause = vi.fn()
    audioApi.instances.push(this)
  }
}

const objectUrl = vi.hoisted(() => ({
  create: vi.fn(() => 'blob:mock-audio'),
  revoke: vi.fn(),
}))

import {
  isSpeechRecognitionSupported,
  isSpeechSynthesisSupported,
  createRecognizer,
  speak,
  stopSpeaking,
} from '@/utils/speech'

/** 浏览器路径专用：显式禁 fetch，jsdom 环境下确定性走 speechSynthesis 同步分支。 */
function stubBrowserOnly() {
  vi.stubGlobal('fetch', undefined)
  vi.stubGlobal('Audio', FakeAudio)
}

/** 云端路径专用：开 fetch + Audio + objectURL，走 speakViaBackend 异步分支。 */
function stubBackendPath() {
  vi.stubGlobal('fetch', vi.fn())
  vi.stubGlobal('Audio', FakeAudio)
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL = objectUrl.create
    static revokeObjectURL = objectUrl.revoke
  })
}

describe('speech 语音工具（ASR 浏览器识别 + TTS 两级降级）', () => {
  beforeEach(() => {
    stopSpeaking() // 清模块级音频单例（先清再 reset 计数）
    vi.clearAllMocks()
    speechApi.utterances.length = 0
    audioApi.instances.length = 0
  })

  afterEach(() => {
    stopSpeaking()
    // 恢复被测试临时删除的全局（见 unsupported 用例）
    vi.stubGlobal('webkitSpeechRecognition', FakeRecognition)
    vi.stubGlobal('speechSynthesis', speechApi.synthesis)
    vi.unstubAllGlobals()
    vi.stubGlobal('webkitSpeechRecognition', FakeRecognition)
    vi.stubGlobal('speechSynthesis', speechApi.synthesis)
    vi.stubGlobal(
      'SpeechSynthesisUtterance',
      class FakeUtterance {
        constructor(text) {
          this.text = text
          this.lang = ''
          this.voice = null
          speechApi.utterances.push(this)
        }
      }
    )
  })

  // ---------- ASR（浏览器识别，行为不变） ----------

  it('支持性探测：识别与合成都可用', () => {
    expect(isSpeechRecognitionSupported()).toBe(true)
    expect(isSpeechSynthesisSupported()).toBe(true)
  })

  it('不支持识别时 createRecognizer 返回 null', () => {
    vi.stubGlobal('webkitSpeechRecognition', undefined)
    vi.stubGlobal('SpeechRecognition', undefined)
    expect(createRecognizer({})).toBeNull()
  })

  it('识别结果回调：拼接 transcript 并 trim', () => {
    const onResult = vi.fn()
    const recognizer = createRecognizer({ onResult })
    expect(recognizer.lang).toBe('zh-CN')

    recognizer.onresult({
      results: [
        [{ transcript: ' 查一下订单 ' }],
        [{ transcript: ' 20260801001 ' }],
      ],
    })
    expect(onResult).toHaveBeenCalledWith('查一下订单 20260801001')
  })

  it('识别结果为空不回调', () => {
    const onResult = vi.fn()
    const recognizer = createRecognizer({ onResult })
    recognizer.onresult({ results: [[{ transcript: '   ' }]] })
    expect(onResult).not.toHaveBeenCalled()
  })

  it('识别错误与结束回调透传', () => {
    const onError = vi.fn()
    const onEnd = vi.fn()
    const recognizer = createRecognizer({ onError, onEnd })
    recognizer.onerror({ error: 'not-allowed' })
    recognizer.onend()
    expect(onError).toHaveBeenCalledWith('not-allowed')
    expect(onEnd).toHaveBeenCalled()
  })

  // ---------- TTS 浏览器路径（后端不可用时的回落） ----------

  it('speak：浏览器路径先打断旧播报再朗读，选中文音色', () => {
    stubBrowserOnly()
    speechApi.synthesis.getVoices.mockReturnValue([
      { lang: 'en-US', name: 'English' },
      { lang: 'zh-CN', name: '中文' },
    ])
    const ok = speak('已为您查询到订单')

    expect(ok).toBe(true)
    expect(speechApi.synthesis.cancel).toHaveBeenCalled()
    expect(speechApi.synthesis.speak).toHaveBeenCalledTimes(1)
    expect(speechApi.utterances).toHaveLength(1)
    expect(speechApi.utterances[0].text).toBe('已为您查询到订单')
    expect(speechApi.utterances[0].voice.name).toBe('中文')
  })

  it('speak：无中文音色时仍用默认音色朗读', () => {
    stubBrowserOnly()
    speechApi.synthesis.getVoices.mockReturnValue([{ lang: 'en-US', name: 'English' }])
    speak('你好')
    expect(speechApi.utterances[0].voice).toBeNull()
    expect(speechApi.utterances[0].lang).toBe('zh-CN')
  })

  it('speak：空文本或完全不可用时返回 false 且不朗读', () => {
    stubBrowserOnly()
    expect(speak('')).toBe(false)
    expect(speechApi.synthesis.speak).not.toHaveBeenCalled()

    // Audio 与 speechSynthesis 都不可用 → false
    vi.stubGlobal('Audio', undefined)
    vi.stubGlobal('speechSynthesis', undefined)
    expect(speak('文本')).toBe(false)
  })

  it('stopSpeaking：调用 cancel 且幂等', () => {
    stopSpeaking()
    stopSpeaking()
    expect(speechApi.synthesis.cancel).toHaveBeenCalledTimes(2)
  })

  // ---------- TTS 云端路径（edge-tts 后端，一级） ----------

  it('speak：云端合成成功 → Audio 播放，不触发浏览器合成', async () => {
    stubBackendPath()
    ttsApi.synthesizeSpeech.mockResolvedValue('blob:mock-audio')

    const ok = speak('订单已发货')
    expect(ok).toBe(true)

    await vi.waitFor(() => expect(audioApi.instances).toHaveLength(1))
    expect(audioApi.instances[0].src).toBe('blob:mock-audio')
    expect(audioApi.instances[0].play).toHaveBeenCalledTimes(1)
    expect(speechApi.synthesis.speak).not.toHaveBeenCalled()
    expect(ttsApi.synthesizeSpeech).toHaveBeenCalledWith('订单已发货')
  })

  it('speak：云端失败 → console.warn 回落浏览器合成（不静默）', async () => {
    stubBackendPath()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    ttsApi.synthesizeSpeech.mockRejectedValue(new Error('语音合成服务暂不可用'))
    speechApi.synthesis.getVoices.mockReturnValue([])

    const ok = speak('退款已到账')
    expect(ok).toBe(true)

    await vi.waitFor(() => expect(speechApi.synthesis.speak).toHaveBeenCalledTimes(1))
    expect(warnSpy).toHaveBeenCalled()
    expect(speechApi.utterances[0].text).toBe('退款已到账')
    warnSpy.mockRestore()
  })

  it('speak：云端失败且浏览器也不可用 → 仅 warn 不抛错', async () => {
    stubBackendPath()
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    ttsApi.synthesizeSpeech.mockRejectedValue(new Error('网络错误'))
    vi.stubGlobal('speechSynthesis', undefined)

    expect(speak('你好')).toBe(true)

    await vi.waitFor(() => expect(warnSpy).toHaveBeenCalled())
    expect(audioApi.instances).toHaveLength(0)
    warnSpy.mockRestore()
  })

  it('stopSpeaking：停止云端音频并释放 objectURL', async () => {
    stubBackendPath()
    ttsApi.synthesizeSpeech.mockResolvedValue('blob:mock-audio')
    speak('播报中')

    await vi.waitFor(() => expect(audioApi.instances).toHaveLength(1))
    stopSpeaking()

    expect(audioApi.instances[0].pause).toHaveBeenCalled()
    expect(objectUrl.revoke).toHaveBeenCalledWith('blob:mock-audio')
    // 幂等：重复 stop 不再释放
    objectUrl.revoke.mockClear()
    stopSpeaking()
    expect(objectUrl.revoke).not.toHaveBeenCalled()
  })

  it('云端音频播完自动释放 objectURL（防泄漏）', async () => {
    stubBackendPath()
    ttsApi.synthesizeSpeech.mockResolvedValue('blob:mock-audio')
    speak('短句')

    await vi.waitFor(() => expect(audioApi.instances).toHaveLength(1))
    audioApi.instances[0].onended()

    expect(objectUrl.revoke).toHaveBeenCalledWith('blob:mock-audio')
  })
})
