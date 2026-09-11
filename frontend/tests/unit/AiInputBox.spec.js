import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

const ElMessageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('element-plus', () => ({ ElMessage: ElMessageMock }))

import AiInputBox from '@/components/Chat/AiInputBox.vue'

// ---------- EP 组件 stub（textarea 风格，与 Chat.spec 同款转发） ----------
const stubs = {
  'el-input': {
    props: ['modelValue'],
    emits: ['update:modelValue', 'keydown'],
    template:
      '<textarea class="el-input-stub" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @keydown="$emit(\'keydown\', $event)" />',
  },
}

function makeHarness(props = {}) {
  let modelValue = ''
  const wrapper = mount(AiInputBox, {
    props: {
      modelValue,
      speakEnabled: false,
      streaming: false,
      ...props,
      'onUpdate:modelValue': (v) => {
        modelValue = v
        wrapper.setProps({ modelValue: v })
      },
    },
    global: { components: stubs },
  })
  return { wrapper, getModel: () => modelValue }
}

/** 捕获最后一次被构造的识别器（连续模式 FakeRec） */
function installFakeRecognition() {
  let lastRecognizer = null
  class FakeRec {
    constructor() {
      this.lang = ''
      this.continuous = false
      this.interimResults = false
      this.onresult = null
      this.onend = null
      this.onerror = null
      lastRecognizer = this
    }
    start() {
      this.started = true
    }
    stop() {
      this.stopped = true
    }
  }
  vi.stubGlobal('webkitSpeechRecognition', FakeRec)
  vi.stubGlobal('speechSynthesis', { cancel: vi.fn(), speak: vi.fn(), getVoices: () => [] })
  return () => lastRecognizer
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('AiInputBox（Trae 风格 AI 对话输入框）', () => {
  it('渲染：placeholder、Enter 提示、空输入发送按钮禁用', () => {
    const { wrapper } = makeHarness()
    expect(wrapper.find('.el-input-stub').attributes('placeholder')).toBe(
      '输入你的问题，或点击麦克风开始语音输入...'
    )
    expect(wrapper.text()).toContain('Enter 发送 · Shift+Enter 换行')
    expect(wrapper.find('.ai-send').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.ai-wave').exists()).toBe(false)
  })

  it('输入有内容：发送按钮点亮可点，点击发送事件', async () => {
    const { wrapper } = makeHarness()
    await wrapper.find('.el-input-stub').setValue('你好')
    await nextTick()
    expect(wrapper.find('.ai-send').attributes('disabled')).toBeUndefined()

    await wrapper.find('.ai-send').trigger('click')
    expect(wrapper.emitted('send')).toBeTruthy()
  })

  it('Enter 发送 / Shift+Enter 不发送', async () => {
    const { wrapper } = makeHarness()
    await wrapper.find('.el-input-stub').setValue('问题')
    const input = wrapper.find('.el-input-stub')

    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toHaveLength(1)

    await input.trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it('点击麦克风：波形 + 「正在聆听」placeholder + 计时出现', async () => {
    installFakeRecognition()
    const { wrapper } = makeHarness()
    await wrapper.find('.ai-mic').trigger('click')
    await nextTick()

    expect(wrapper.find('.ai-wave').exists()).toBe(true)
    expect(wrapper.find('.ai-elapsed').text()).toBe('0:00')
    expect(wrapper.find('.el-input-stub').attributes('placeholder')).toBe('正在聆听…')
    expect(wrapper.find('.ai-mic').classes()).toContain('is-listening')
  })

  it('聆听中中间结果实时预览；再次点击麦克风停止 → 结果落框、波形消失', async () => {
    const getLastRec = installFakeRecognition()
    const { wrapper, getModel } = makeHarness()
    await wrapper.find('.ai-mic').trigger('click')
    const rec = getLastRec()

    // interim 预览
    rec.onresult({ results: [{ isFinal: false, 0: { transcript: '正在识别' } }] })
    await nextTick()
    expect(wrapper.find('.el-input-stub').element.value).toBe('正在识别')

    // isFinal 段 + 停止
    rec.onresult({ results: [{ isFinal: true, 0: { transcript: '查一下订单' } }] })
    await wrapper.find('.ai-mic').trigger('click')
    rec.onend()
    await nextTick()

    expect(wrapper.find('.ai-wave').exists()).toBe(false)
    expect(getModel()).toBe('查一下订单')
  })

  it('多段识别结果以空格拼接落框', async () => {
    const getLastRec = installFakeRecognition()
    const { wrapper, getModel } = makeHarness()
    await wrapper.find('.ai-mic').trigger('click')
    const rec = getLastRec()
    rec.onresult({ results: [{ isFinal: true, 0: { transcript: '查一下订单' } }] })
    rec.onresult({ results: [{ isFinal: true, 0: { transcript: '20260801001' } }] })
    await wrapper.find('.ai-mic').trigger('click')
    rec.onend()
    await nextTick()
    expect(getModel()).toBe('查一下订单 20260801001')
  })

  it('识别为空：提示「未识别到内容」，不写入任何内容', async () => {
    const getLastRec = installFakeRecognition()
    const { wrapper, getModel } = makeHarness()
    await wrapper.find('.ai-mic').trigger('click')
    const rec = getLastRec()
    await wrapper.find('.ai-mic').trigger('click')
    rec.onend()
    await nextTick()
    expect(ElMessageMock.warning).toHaveBeenCalledWith(expect.stringContaining('未识别到内容'))
    expect(getModel()).toBe('')
  })

  it('Esc 取消：不写入任何内容', async () => {
    const getLastRec = installFakeRecognition()
    const { wrapper, getModel } = makeHarness()
    await wrapper.find('.ai-mic').trigger('click')
    const rec = getLastRec()
    rec.onresult({ results: [{ isFinal: true, 0: { transcript: '这段将被丢弃' } }] })
    await wrapper.find('.el-input-stub').trigger('keydown', { key: 'Escape' })
    rec.onend()
    await nextTick()
    expect(wrapper.find('.ai-wave').exists()).toBe(false)
    expect(ElMessageMock.warning).not.toHaveBeenCalled()
    expect(getModel()).toBe('')
  })

  it('不支持语音识别的浏览器：点击麦克风弹提示，不进入聆听', async () => {
    vi.stubGlobal('webkitSpeechRecognition', undefined)
    vi.stubGlobal('SpeechRecognition', undefined)
    vi.stubGlobal('speechSynthesis', { cancel: vi.fn(), speak: vi.fn() })
    const { wrapper } = makeHarness()
    await wrapper.find('.ai-mic').trigger('click')
    expect(ElMessageMock.warning).toHaveBeenCalledWith(
      expect.stringContaining('不支持语音识别')
    )
    expect(wrapper.find('.ai-wave').exists()).toBe(false)
  })

  it('权限拒绝：明确报错，聆听态复位', async () => {
    const getLastRec = installFakeRecognition()
    const { wrapper } = makeHarness()
    await wrapper.find('.ai-mic').trigger('click')
    getLastRec().onerror({ error: 'not-allowed' })
    await nextTick()
    expect(ElMessageMock.error).toHaveBeenCalledWith(
      expect.stringContaining('麦克风权限')
    )
    expect(wrapper.find('.ai-wave').exists()).toBe(false)
  })

  it('聆听中点击发送：先停止录音并发送', async () => {
    const getLastRec = installFakeRecognition()
    const { wrapper, getModel } = makeHarness()
    await wrapper.find('.ai-mic').trigger('click')
    const rec = getLastRec()
    rec.onresult({ results: [{ isFinal: true, 0: { transcript: '语音问题' } }] })

    await wrapper.find('.ai-send').trigger('click')
    // 停止录音（结果落框）
    rec.onend()
    await nextTick()
    expect(getModel()).toBe('语音问题')
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it('播报开关点击发出 speak-toggle', async () => {
    const { wrapper } = makeHarness()
    await wrapper.find('.ai-speak').trigger('click')
    expect(wrapper.emitted('speak-toggle')).toHaveLength(1)
  })

  it('streaming 期间按钮变为「停止生成」：可点、红色停止态、点击发出 stop 而非 send', async () => {
    const { wrapper } = makeHarness({ streaming: true, modelValue: '内容' })
    const btn = wrapper.find('.ai-send')
    // 停止按钮必须可点（不像发送态那样禁用）
    expect(btn.attributes('disabled')).toBeUndefined()
    expect(btn.classes()).toContain('is-stop')
    expect(btn.attributes('data-tip')).toBe('停止生成')
    // 停止图标（方块）
    expect(wrapper.find('.ai-stop-icon').exists()).toBe(true)

    await btn.trigger('click')
    expect(wrapper.emitted('stop')).toHaveLength(1)
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('非 streaming 时按钮为发送态：图标为箭头、无 stop 图标', () => {
    const { wrapper } = makeHarness({ streaming: false, modelValue: '内容' })
    const btn = wrapper.find('.ai-send')
    expect(btn.classes()).not.toContain('is-stop')
    expect(wrapper.find('.ai-stop-icon').exists()).toBe(false)
  })
})
describe('AiInputBox 聆听中发送（识别结果一并提交）', () => {
  it('聆听中回车：识别结果落盘后才触发 send（不再把旧文本发出去）', async () => {
    const getLastRec = installFakeRecognition()
    const { wrapper, getModel } = makeHarness()
    await wrapper.find('.ai-mic').trigger('click')
    const rec = getLastRec()
    rec.onresult({ results: [{ isFinal: true, 0: { transcript: '语音提问' } }] })

    await wrapper.find('.el-input-stub').trigger('keydown', { key: 'Enter' })
    // onend（结果落框）之前不发送
    expect(wrapper.emitted('send')).toBeUndefined()

    rec.onend()
    await nextTick()
    expect(getModel()).toBe('语音提问')
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it('聆听中点击发送：识别结果一并提交', async () => {
    const getLastRec = installFakeRecognition()
    const { wrapper, getModel } = makeHarness()
    await wrapper.find('.ai-mic').trigger('click')
    const rec = getLastRec()
    rec.onresult({ results: [{ isFinal: true, 0: { transcript: '语音提问二' } }] })

    await wrapper.find('.ai-send').trigger('click')
    expect(wrapper.emitted('send')).toBeUndefined()

    rec.onend()
    await nextTick()
    expect(getModel()).toBe('语音提问二')
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it('聆听中再次点击麦克风：仅落盘结果，不触发 send', async () => {
    const getLastRec = installFakeRecognition()
    const { wrapper, getModel } = makeHarness()
    await wrapper.find('.ai-mic').trigger('click')
    const rec = getLastRec()
    rec.onresult({ results: [{ isFinal: true, 0: { transcript: '只是记录' } }] })

    await wrapper.find('.ai-mic').trigger('click')
    rec.onend()
    await nextTick()

    expect(getModel()).toBe('只是记录')
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('onend 丢失（aborted/设备冲突）时兜底定时器收尾：仍落盘并发送', async () => {
    vi.useFakeTimers()
    try {
      const getLastRec = installFakeRecognition()
      const { wrapper, getModel } = makeHarness()
      await wrapper.find('.ai-mic').trigger('click')
      const rec = getLastRec()
      rec.onresult({ results: [{ isFinal: true, 0: { transcript: '语音兜底' } }] })

      await wrapper.find('.el-input-stub').trigger('keydown', { key: 'Enter' })
      // onend 未触发：发送意图先不生效（防旧文本误发）
      expect(wrapper.emitted('send')).toBeUndefined()
      expect(wrapper.find('.ai-wave').exists()).toBe(true)

      // 2.5s 兜底定时器触发：手动收尾并发送
      await vi.advanceTimersByTimeAsync(2500)
      await nextTick()

      expect(getModel()).toBe('语音兜底')
      expect(wrapper.emitted('send')).toHaveLength(1)
      expect(wrapper.find('.ai-wave').exists()).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('兜底定时器先收尾后，迟到的 onend 被忽略：不弹虚假警告、不重复发送', async () => {
    vi.useFakeTimers()
    try {
      const getLastRec = installFakeRecognition()
      const { wrapper, getModel } = makeHarness()
      await wrapper.find('.ai-mic').trigger('click')
      const rec = getLastRec()
      rec.onresult({ results: [{ isFinal: true, 0: { transcript: '定时器先收尾' } }] })

      await wrapper.find('.el-input-stub').trigger('keydown', { key: 'Enter' })
      // onend 迟到未触发：2.5s 兜底定时器先收尾发送
      await vi.advanceTimersByTimeAsync(2500)
      await nextTick()
      expect(wrapper.emitted('send')).toHaveLength(1)
      expect(getModel()).toBe('定时器先收尾')

      // 迟到的 onend（>2.5s 才到）：会话已被 resetRecognition 收尾，陈旧回调直接忽略
      rec.onend()
      await nextTick()

      // 不弹「未识别到内容」虚假警告（此前后会走普通路径误报）、不重复发送
      expect(ElMessageMock.warning).not.toHaveBeenCalledWith('未识别到内容，请重试')
      expect(wrapper.emitted('send')).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('聆听中发送但识别为空：输入框已有文本不吞，直接发送该文本', async () => {
    const getLastRec = installFakeRecognition()
    const { wrapper, getModel } = makeHarness()
    await wrapper.find('.el-input-stub').setValue('文字问题')
    await nextTick()

    await wrapper.find('.ai-mic').trigger('click')
    const rec = getLastRec()
    // 识别无结果，用户点发送
    await wrapper.find('.ai-send').trigger('click')
    expect(wrapper.emitted('send')).toBeUndefined()

    rec.onend()
    await nextTick()

    expect(getModel()).toBe('文字问题')
    expect(wrapper.emitted('send')).toHaveLength(1)
    expect(ElMessageMock.warning).not.toHaveBeenCalled()
  })

  it('兜底收尾会复位 sendAfterStop：下一次识别不会因历史状态自动发送', async () => {
    vi.useFakeTimers()
    try {
      const getLastRec = installFakeRecognition()
      const { wrapper, getModel } = makeHarness()
      // 第一轮：聆听中回车（有识别内容）且 onend 丢失 → 兜底定时器收尾并复位
      await wrapper.find('.ai-mic').trigger('click')
      getLastRec().onresult({ results: [{ isFinal: true, 0: { transcript: '第一轮内容' } }] })
      await wrapper.find('.el-input-stub').trigger('keydown', { key: 'Enter' })
      await vi.advanceTimersByTimeAsync(2500)
      await nextTick()
      expect(wrapper.emitted('send')).toHaveLength(1)
      expect(getModel()).toBe('第一轮内容')

      // 第二轮：重新聆听并正常停止（无发送意图）→ 不应自动发送
      await wrapper.find('.ai-mic').trigger('click')
      const rec = getLastRec()
      await wrapper.find('.ai-mic').trigger('click')
      rec.onend()
      await nextTick()
      expect(wrapper.emitted('send')).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
