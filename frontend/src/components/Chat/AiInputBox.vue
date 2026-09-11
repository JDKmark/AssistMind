<!--
  AiInputBox —— AI 对话输入框（视觉对齐 Trae Work：简洁、收敛、专业）

  - textarea 多行自动增高（1→8 行，超出内部滚动），Enter 发送 / Shift+Enter 换行
  - 发送按钮状态机：空输入禁用 → 有内容主题渐变点亮（hover 微放大）→ 发送后回禁用
  - 语音输入：点击麦克风进入聆听（波形动画 + 计时 + placeholder「正在聆听…」），
    连续识别（continuous + interim 实时预览），再次点击麦克风 / 发送 / Esc 结束：
      麦克风或发送结束 → 识别结果写入输入框；Esc / 聆听中手动输入 → 丢弃不写入
  - 边界：不支持语音识别的浏览器点击提示；未识别到内容提示；麦克风权限拒绝明确报错
  - 深浅色模式随系统自动适配；设计令牌集中为 CSS 变量（--ai-*）
-->
<template>
  <div class="ai-input" :class="{ 'is-listening': listening }">
    <!-- 聆听态：频谱波形 + 已录音时长 -->
    <div v-if="listening" class="ai-wave" role="status" aria-label="正在聆听">
      <span
        v-for="(b, i) in bars"
        :key="i"
        class="ai-bar"
        :style="{ height: b.h + 'px', animationDuration: b.dur + 's', animationDelay: b.delay + 's' }"
      />
      <span class="ai-elapsed">{{ elapsed }}</span>
    </div>

    <el-input
      ref="inputRef"
      v-model="displayText"
      type="textarea"
      :autosize="{ minRows: 1, maxRows: 8 }"
      class="ai-input-field"
      :placeholder="listening ? '正在聆听…' : '输入你的问题，或点击麦克风开始语音输入...'"
      @keydown.enter.exact.prevent="onEnter"
      @keydown.esc="onEsc"
    />

    <div class="ai-toolbar">
      <div class="ai-toolbar-left">
        <!-- 答案朗读开关（TTS 两级降级的一级开关） -->
        <button
          v-if="speakSupported"
          class="ai-icobtn ai-speak"
          :class="{ 'is-on': speakEnabled }"
          :aria-pressed="speakEnabled"
          data-tip="答案朗读"
          @click="$emit('speak-toggle')"
        >
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 5 6 9H2v6h4l5 4V5z" />
            <path d="M15.5 8.5a5 5 0 0 1 0 7" />
            <path d="M18.5 5.5a9 9 0 0 1 0 13" />
          </svg>
        </button>
        <!-- 语音输入 -->
        <button class="ai-icobtn ai-mic" :class="{ 'is-listening': listening }" data-tip="语音输入" @click="onMicClick">
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
          </svg>
        </button>
      </div>

      <div class="ai-toolbar-right">
        <span class="ai-hint">Enter 发送 · Shift+Enter 换行</span>
        <!-- 发送/停止状态机：流式生成中按钮变为「停止生成」（红色方块），点击中止当前流 -->
        <button
          class="ai-send"
          :class="{ 'is-stop': streaming }"
          :disabled="streaming ? false : !canSend"
          :aria-disabled="streaming ? false : !canSend"
          :data-tip="streaming ? '停止生成' : '发送'"
          @click="onSendClick"
        >
          <svg
            v-if="streaming"
            class="ai-stop-icon"
            viewBox="0 0 24 24"
            width="15"
            height="15"
            aria-hidden="true"
          >
            <rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" />
          </svg>
          <svg v-else viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 19V5" />
            <path d="m5 12 7-7 7 7" />
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onDeactivated, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  isSpeechRecognitionSupported,
  isSpeechSynthesisSupported,
  stopSpeaking,
} from '@/utils/speech'

const props = defineProps({
  modelValue: { type: String, default: '' },
  speakEnabled: { type: Boolean, default: false },
  streaming: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'send', 'stop', 'speak-toggle'])

const inputRef = ref(null)
const voiceSupported = isSpeechRecognitionSupported()
const speakSupported = isSpeechSynthesisSupported()

// ---------- 输入框：模型值 + 聆听态实时预览 ----------
const editMode = ref(false) // 聆听中用户手动输入 → 丢弃识别预览
const listening = ref(false)
const finalSegs = ref([]) // 连续识别已确认文本段（段间以空格拼接，还原语流间隔）
const interimText = ref('') // 实时中间结果
const cancelled = ref(false)
// 发送触发停止录音时置位：onend 落盘识别文本后再 emit('send')（一并提交）
const sendAfterStop = ref(false)

const displayText = computed({
  get() {
    if (!listening.value || editMode.value) return props.modelValue
    const segs = [...finalSegs.value]
    if (interimText.value) segs.push(interimText.value)
    const rec = segs.join(' ').trim()
    if (!rec) return props.modelValue
    return props.modelValue ? `${props.modelValue} ${rec}` : rec
  },
  set(v) {
    // 聆听中手动输入：终止识别并丢弃预览，仅保留用户输入
    if (listening.value && !editMode.value) {
      editMode.value = true
      cancelListening()
    }
    emit('update:modelValue', v)
  },
})

const canSend = computed(
  () => (props.modelValue.trim().length > 0 || listening.value) && !props.streaming
)

// ---------- 语音识别（Web Speech API，连续模式） ----------
let recognizer = null
// 「发送时结束录音」的兜底定时器：onend 异常丢失时保证发送动作不落空
let sendTimer = null

function startListening() {
  stopSpeaking() // ASR/TTS 互斥：识别前先打断任何播报，防麦克风自循环
  const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!Ctor) {
    ElMessage.warning('当前浏览器不支持语音识别，请使用 Chrome/Edge')
    return
  }
  clearSendTimer()
  sendAfterStop.value = false // 复位：上一轮「发送停止录音」若因 onend 丢失未复位，不得泄漏到本次识别
  const rec = new Ctor()
  rec.lang = 'zh-CN'
  rec.continuous = true
  rec.interimResults = true
  rec.onresult = (event) => {
    let final = ''
    let interim = ''
    for (const r of Array.from(event.results)) {
      if (r.isFinal) final += r[0].transcript
      else interim += r[0].transcript
    }
    if (final) finalSegs.value.push(final.trim())
    interimText.value = interim
  }
  rec.onerror = (event) => {
    if (!recognizer || recognizer !== rec) return // 陈旧回调防御（同 onend，见下）
    const err = event?.error || ''
    if (err === 'not-allowed') {
      cancelled.value = true
      listening.value = false
      stopTimer()
      ElMessage.error('麦克风权限被拒绝，请在浏览器设置中允许')
    } else if (err === 'no-speech') {
      listening.value = false
      stopTimer()
      ElMessage.warning('未识别到内容，请重试')
    } else if (err !== 'aborted') {
      listening.value = false
      stopTimer()
      ElMessage.warning(`语音识别失败：${err}`)
    }
  }
  rec.onend = () => {
    // 陈旧回调防御：本会话已收尾（resetRecognition 置空 recognizer）或已被新
    // 识别会话替换时，迟到的 onend 直接忽略——否则兜底定时器先收尾后，迟到
    // 的 onend 会走普通路径，在消息已发出后弹出虚假的「未识别到内容」警告
    if (!recognizer || recognizer !== rec) return
    listening.value = false
    stopTimer()
    // 「发送时结束录音」路径：统一在 finalizeSendAfterStop 里落盘+提交
    // （onend 可能因 aborted 等异常不触发，由 stopListeningAndSend 的兜底定时器接管）
    if (sendAfterStop.value) {
      finalizeSendAfterStop()
      return
    }
    const got = finalSegs.value.join(' ').trim()
    if (cancelled.value || !got) {
      if (!cancelled.value && !got) ElMessage.warning('未识别到内容，请重试')
      resetRecognition()
      return
    }
    const base = props.modelValue.trim()
    emit('update:modelValue', base ? `${base} ${got}` : got)
    resetRecognition()
    focusInput()
  }
  recognizer = rec
  finalSegs.value = []
  interimText.value = ''
  cancelled.value = false
  editMode.value = false
  listening.value = true
  startTimer()
  try {
    rec.start()
  } catch (e) {
    listening.value = false
    stopTimer()
    ElMessage.warning('语音启动失败，请重试')
  }
}

function stopListening() {
  // 再次点击麦克风：仅结束录音并落盘结果
  cancelled.value = false
  recognizer?.stop() // onend 中落盘
}

function stopListeningAndSend() {
  // 发送按钮/回车触发：结束录音，识别结果在 onend 落盘后一并提交
  cancelled.value = false
  sendAfterStop.value = true
  recognizer?.stop()
  // 兜底：Web Speech API 在 aborted / 设备冲突等异常下 onend 可能不触发，
  // 若无兜底会永久滞留在聆听态并吞掉发送意图。2.5s 内 onend 未触发（未复位
  // sendAfterStop）则手动收尾——保证「点了发送」这个动作一定有一次执行。
  clearSendTimer()
  sendTimer = setTimeout(() => {
    if (sendAfterStop.value) finalizeSendAfterStop()
  }, 2500)
}

function finalizeSendAfterStop() {
  // 「发送时结束录音」的收尾：合入识别文本（如有）→ 复位 → 提交发送。
  // 识别为空但输入框已有文本时不吞掉发送意图（此前 !got 直接 return 会丢文本）。
  clearSendTimer()
  listening.value = false
  stopTimer()
  const got = finalSegs.value.join(' ').trim()
  const base = props.modelValue.trim()
  const hasSendable = Boolean(base || got)
  if (got) emit('update:modelValue', base ? `${base} ${got}` : got)
  resetRecognition()
  if (!hasSendable) {
    ElMessage.warning('未识别到内容，请重试')
    focusInput()
    return
  }
  emit('send')
  focusInput()
}

function clearSendTimer() {
  if (sendTimer) {
    clearTimeout(sendTimer)
    sendTimer = null
  }
}

function cancelListening() {
  cancelled.value = true
  sendAfterStop.value = false
  clearSendTimer()
  recognizer?.stop()
}

function resetRecognition() {
  clearSendTimer()
  recognizer = null
  finalSegs.value = []
  interimText.value = ''
  cancelled.value = false
  sendAfterStop.value = false
}

function focusInput() {
  inputRef.value?.focus?.()
}

// ---------- 聆听计时 mm:ss ----------
const elapsed = ref('0:00')
let timerHandle = null

function startTimer() {
  const t0 = Date.now()
  elapsed.value = '0:00'
  timerHandle = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000)
    elapsed.value = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  }, 1000)
}
function stopTimer() {
  if (timerHandle) {
    clearInterval(timerHandle)
    timerHandle = null
  }
}

// ---------- 频谱波形（14 根错峰竖条，0.5-0.8s 循环） ----------
const bars = Array.from({ length: 14 }, (_, i) => ({
  dur: 0.5 + ((i * 7) % 5) * 0.07,
  delay: -(i * 0.09),
  h: 9 + ((i * 7) % 12),
}))

// ---------- 事件 ----------
function onMicClick() {
  if (listening.value) stopListening()
  else startListening()
}

function onEnter() {
  if (listening.value) {
    stopListeningAndSend() // 发送结束录音，识别结果在 onend 一并提交
    return
  }
  if (canSend.value) emit('send')
}

function onSendClick() {
  // 流式生成中：按钮为「停止生成」，点击中止在途流
  if (props.streaming) {
    emit('stop')
    return
  }
  if (listening.value) {
    stopListeningAndSend()
    return
  }
  if (canSend.value) emit('send')
}

function onEsc() {
  if (listening.value) cancelListening() // Esc 取消：不写入任何内容
}

onBeforeUnmount(() => {
  recognizer?.stop()
  clearSendTimer()
  stopTimer()
})

onDeactivated(() => {
  // keep-alive 切走页面：聆听中按取消收尾（不写入输入框），识别与计时都不留后台
  if (listening.value) cancelListening()
  clearSendTimer()
  stopTimer()
})
</script>

<style scoped>
/* ============ 设计令牌（集中管理，深浅色统一适配） ============ */
.ai-input {
  --ai-bg: rgba(255, 255, 255, 0.88);
  --ai-bg-solid: #ffffff;
  --ai-border: #e5e7eb;
  --ai-text: #111827;
  --ai-text-muted: #9ca3af;
  --ai-primary: #2352c5;
  --ai-primary-soft: rgba(35, 82, 197, 0.1);
  --ai-gradient: linear-gradient(135deg, #2352c5 0%, #3565d9 100%);
  --ai-glow: rgba(35, 82, 197, 0.28);
  --ai-shadow: 0 2px 10px rgba(17, 24, 39, 0.06);
  --ai-shadow-focus: 0 8px 28px rgba(17, 24, 39, 0.12);
  --ai-danger: #ef4444;
}
/* 暗色模式：全站暂未提供暗色主题，此处不再单独响应 prefers-color-scheme，
   避免暗色系统下输入框变黑、页面仍为亮色的视觉断裂（P1-2，全站暗色为独立需求） */

/* ============ 外层容器 ============ */
.ai-input {
  width: 100%;
  max-width: 1024px;
  margin: 0 auto;
  background: var(--ai-bg);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--ai-border);
  border-radius: 14px;
  padding: 10px 12px 8px;
  color: var(--ai-text);
  box-shadow: var(--ai-shadow);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.ai-input:focus-within {
  border-color: var(--ai-primary);
  box-shadow: var(--ai-shadow-focus), 0 0 0 3px var(--ai-glow);
}

/* textarea 融入容器（去 el-input 内边框） */
.ai-input :deep(.el-textarea__inner) {
  border: none;
  box-shadow: none;
  background: transparent;
  resize: none;
  padding: 2px 4px;
  color: var(--ai-text);
  line-height: 1.6;
}
.ai-input :deep(.el-textarea__inner::placeholder) {
  color: var(--ai-text-muted);
}
.ai-input.is-listening :deep(.el-textarea__inner) {
  caret-color: var(--ai-primary);
}

/* ============ 聆听态：频谱波形 + 计时 ============ */
.ai-wave {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 26px;
  padding: 4px 4px 0;
  margin-bottom: 2px;
}
.ai-bar {
  width: 3px;
  border-radius: 2px;
  background: var(--ai-primary);
  transform-origin: bottom;
  animation-name: ai-wave;
  animation-timing-function: ease-in-out;
  animation-iteration-count: infinite;
  animation-direction: alternate;
  opacity: 0.9;
}
@keyframes ai-wave {
  from {
    transform: scaleY(0.3);
  }
  to {
    transform: scaleY(1);
  }
}
.ai-elapsed {
  margin-left: auto;
  font-size: 12px;
  color: var(--ai-text-muted);
  font-variant-numeric: tabular-nums;
  align-self: center;
}

/* ============ 底部工具栏 ============ */
.ai-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}
.ai-toolbar-left {
  display: flex;
  align-items: center;
  gap: 2px;
}

/* 图标按钮：hover 圆角高亮 + 0.2s 过渡 */
.ai-icobtn,
.ai-send {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  background: transparent;
  color: var(--ai-text-muted);
  transition: background 0.2s ease, color 0.2s ease, transform 0.2s ease,
    box-shadow 0.2s ease, opacity 0.2s ease;
}
.ai-icobtn:hover {
  background: var(--ai-primary-soft);
  color: var(--ai-primary);
}
.ai-icobtn:active {
  transform: scale(0.94);
}
/* 播报开启：主题色点亮 */
.ai-icobtn.ai-speak.is-on {
  color: var(--ai-primary);
  background: var(--ai-primary-soft);
}
/* 聆听中：麦克风红色高亮 + 呼吸环 */
.ai-icobtn.ai-mic.is-listening {
  color: #fff;
  background: var(--ai-danger);
  animation: ai-pulse 1.2s ease-in-out infinite;
}
@keyframes ai-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.45);
  }
  50% {
    box-shadow: 0 0 0 5px rgba(239, 68, 68, 0);
  }
}

/* CSS tooltip（hover / focus 显示） */
.ai-icobtn::after,
.ai-send::after {
  content: attr(data-tip);
  position: absolute;
  bottom: calc(100% + 8px);
  left: 50%;
  transform: translateX(-50%) translateY(2px);
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.4;
  white-space: nowrap;
  background: var(--ai-bg-solid);
  color: var(--ai-text);
  border: 1px solid var(--ai-border);
  box-shadow: var(--ai-shadow);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s ease, transform 0.15s ease;
  z-index: 10;
}
.ai-icobtn:hover::after,
.ai-icobtn:focus-visible::after,
.ai-send:hover:not(:disabled)::after,
.ai-send:focus-visible:not(:disabled)::after {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}

/* ============ 发送按钮状态机 ============ */
.ai-send {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  margin-left: 8px;
  background: var(--ai-gradient);
  color: #fff;
  box-shadow: 0 3px 10px rgba(35, 82, 197, 0.32);
}
.ai-send:hover:not(:disabled) {
  transform: scale(1.06);
  box-shadow: 0 5px 16px rgba(35, 82, 197, 0.42);
}
.ai-send:active:not(:disabled) {
  transform: scale(0.97);
}
.ai-send:disabled {
  background: var(--ai-border);
  color: var(--ai-text-muted);
  box-shadow: none;
  cursor: not-allowed;
  opacity: 0.6;
  transform: none;
}
/* 停止生成态：红色方块，与发送态形成明确反差 */
.ai-send.is-stop {
  background: var(--ai-danger);
  box-shadow: 0 3px 10px rgba(239, 68, 68, 0.32);
}
.ai-send.is-stop:hover:not(:disabled) {
  transform: scale(1.06);
  box-shadow: 0 5px 16px rgba(239, 68, 68, 0.42);
}

.ai-hint {
  font-size: 12px;
  color: var(--ai-text-muted);
}
@media (max-width: 900px) {
  .ai-hint {
    display: none;
  }
}
/* 移动端触控目标放大（P1-3）：桌面密度不变 */
@media (max-width: 767px) {
  .ai-icobtn {
    width: 40px;
    height: 40px;
  }
  .ai-send {
    width: 40px;
    height: 40px;
    margin-left: 4px;
  }
}
</style>