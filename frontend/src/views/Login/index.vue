<template>
  <div class="login-page" ref="rootRef">
    <!-- 左：品牌面板（深墨蓝 + 蓝图点阵） -->
    <div class="brand-panel am-grid-dark">
      <!-- 顶部柔光（GSAP 缓动光斑，替代原 ::before） -->
      <div class="glow-orb"></div>
      <div class="brand-top">
        <div class="brand-mark">
          <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="10" cy="22" r="3" fill="#fff" />
            <circle cx="22" cy="22" r="3" fill="#fff" />
            <circle cx="16" cy="9" r="3" style="fill: var(--am-blue-400)" />
            <path
              d="M16 12 L11 19.5 M16 12 L21 19.5"
              stroke="#fff"
              stroke-width="1.8"
              stroke-linecap="round"
            />
          </svg>
        </div>
        <span class="brand-name">AssistMind</span>
      </div>

      <div class="brand-hero">
        <h1 class="hero-title">让文档<br />开口回答</h1>
        <p class="hero-sub">SaaS 产品文档智能问答客服系统</p>

        <!-- 迷你客服对话预览（纯 CSS 绘制，示意产品形态） -->
        <div class="hero-mini" aria-hidden="true">
          <div class="mini-ai-row">
            <span class="mini-ai-dot"></span>
            <span class="mini-ai-bubble">您好，我是 AssistMind，请问有什么可以帮您？</span>
          </div>
          <div class="mini-user-row">
            <span class="mini-user-bubble">华为 Mate 70 Pro 多少钱？</span>
          </div>
          <div class="mini-ai-row">
            <span class="mini-ai-dot"></span>
            <span class="mini-ai-bubble">起售价 6999 元，支持 24 期免息，下单 48 小时内发货。</span>
          </div>
          <div class="mini-input">
            <span class="mini-input-dot"></span>
            <em>输入你的问题…</em>
          </div>
        </div>

        <ul class="feature-list">
          <li>
            <span class="feature-idx">01</span>
            <span class="feature-name">混合检索</span>
            <span class="feature-desc">向量 + BM25 双路召回，重排截断</span>
          </li>
          <li>
            <span class="feature-idx">02</span>
            <span class="feature-name">工具编排</span>
            <span class="feature-desc">MCP 协议驱动订单 / 物流 / 售后</span>
          </li>
          <li>
            <span class="feature-idx">03</span>
            <span class="feature-name">根因诊断</span>
            <span class="feature-desc">指标 · 日志 · 变更证据链推理</span>
          </li>
        </ul>
      </div>

      <div class="brand-foot">
        <span class="am-mono">RAG · MCP · LangGraph</span>
      </div>
    </div>

    <!-- 右：登录表单（云白） -->
    <div class="form-panel">
      <div class="form-box">
        <h2 class="form-title">欢迎回来</h2>
        <p class="form-sub">登录后进入智能客服工作台</p>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-width="0"
          @submit.prevent="handleLogin"
        >
          <el-form-item prop="username">
            <el-input
              v-model="form.username"
              placeholder="用户名"
              :prefix-icon="User"
              size="large"
              @keyup.enter="handleLogin"
            />
          </el-form-item>
          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="密码"
              :prefix-icon="Lock"
              size="large"
              show-password
              @keyup.enter="handleLogin"
            />
          </el-form-item>
          <el-form-item>
            <el-button
              type="primary"
              size="large"
              class="login-btn"
              :loading="loading"
              @click="handleLogin"
            >
              登录
            </el-button>
          </el-form-item>
        </el-form>

        <div class="demo-accounts">
          <div class="demo-caption">演示账号 · 点击快速填入</div>
          <div class="demo-chips">
            <div
              v-for="acc in DEMO_ACCOUNTS"
              :key="acc.username"
              class="demo-chip"
              role="button"
              tabindex="0"
              @click="fillAccount(acc)"
              @keydown.enter.prevent="fillAccount(acc)"
              @keydown.space.prevent="fillAccount(acc)"
            >
              <span class="demo-role">{{ acc.label }}</span>
              <span class="am-mono demo-cred">{{ acc.username }} / {{ acc.password }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import gsap from 'gsap'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { User, Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const router = useRouter()
const auth = useAuthStore()
const formRef = ref()
const loading = ref(false)
const rootRef = ref(null)

// ---------- GSAP 登录页入场动画（一次性 + 慢速漂浮，非循环干扰） ----------
// 初始隐藏态全部由 gsap.set 管理：JS 失败 / 动画被禁用时元素保持可见；
// prefers-reduced-motion 下整个动画组跳过（matchMedia 守卫）。
let animCtx = null

onMounted(() => {
  // 选择器均限登录页内部且全页唯一，无需 scope（传 rootRef 反而引入 ref 对象非 Element 的隐患）
  animCtx = gsap.context(() => {
    const mm = gsap.matchMedia()
    mm.add('(prefers-reduced-motion: no-preference)', () => {
      // logo 神经连线路径勾勒（按实际长度设 dash，两段子路径完整描出）
      const path = document.querySelector('.brand-mark path')
      const len = path ? path.getTotalLength() : 0
      if (path && len) {
        gsap.set(path, { strokeDasharray: len, strokeDashoffset: len })
      }

      const tl = gsap.timeline({ defaults: { ease: 'power3.out' } })
      tl.to('.brand-mark path', { strokeDashoffset: 0, duration: 0.9, ease: 'power2.inOut' }, 0)
        .from('.brand-mark circle', {
          scale: 0,
          transformOrigin: 'center',
          duration: 0.35,
          stagger: 0.06,
          ease: 'back.out(2)',
        }, 0.12)
        .from('.brand-name', { y: 10, autoAlpha: 0, duration: 0.5 }, 0.4)
        .from('.hero-title', { y: 18, autoAlpha: 0, duration: 0.6 }, 0.5)
        .from('.hero-sub', { y: 12, autoAlpha: 0, duration: 0.5 }, 0.65)
        .from('.hero-mini', { y: 16, autoAlpha: 0, duration: 0.55 }, 0.74)
        .from('.feature-list li', { y: 14, autoAlpha: 0, duration: 0.45, stagger: 0.09 }, 0.86)
        .from('.brand-foot', { autoAlpha: 0, duration: 0.4 }, 1.1)
        .from('.form-box', { y: 18, autoAlpha: 0, duration: 0.55 }, 0.6)

      // 慢速漂浮（非循环入场之外的氛围层）：光斑 52s 往返、logo 呼吸 4.5s
      gsap.to('.glow-orb', { x: 110, y: 80, duration: 26, yoyo: true, repeat: -1, ease: 'sine.inOut' })
      gsap.to('.brand-mark', { y: -5, duration: 4.5, yoyo: true, repeat: -1, ease: 'sine.inOut' })
    })
  })
})

onBeforeUnmount(() => {
  animCtx && animCtx.revert()
})

const form = reactive({
  username: '',
  password: '',
})

// 演示账号：点击行快速填入表单
const DEMO_ACCOUNTS = [
  { label: '管理员', username: 'admin', password: 'admin123' },
  { label: '客服', username: 'agent', password: 'agent123' },
  { label: '用户1', username: 'user1', password: 'user1123' },
  { label: '用户2', username: 'user2', password: 'user2123' },
]

function fillAccount(acc) {
  form.username = acc.username
  form.password = acc.password
}

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  await formRef.value.validate(async (valid) => {
    if (!valid) return
    loading.value = true
    try {
      await auth.login(form.username, form.password)
      ElMessage.success('登录成功')
      router.push('/chat')
    } catch (e) {
      // 错误已在 request.js 拦截器处理
    } finally {
      loading.value = false
    }
  })
}
</script>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
}

/* ---------- 左：品牌面板 ---------- */
.brand-panel {
  flex: 0 0 40%;
  max-width: 680px;
  min-width: 0;
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 44px 56px;
  background: linear-gradient(160deg, var(--am-ink-900) 0%, var(--am-ink-950) 100%);
  color: #fff;
  overflow: hidden;
}
/* 深色底 + 蓝图点阵（双背景层：点阵叠在墨蓝渐变之上，避免覆盖） */
.brand-panel.am-grid-dark {
  background-image: radial-gradient(
      circle,
      color-mix(in srgb, var(--am-blue-400) 16%, transparent) 1px,
      transparent 1px
    ),
    linear-gradient(160deg, var(--am-ink-900) 0%, var(--am-ink-950) 100%);
  background-size: 24px 24px, 100% 100%;
}
/* 顶部柔光（GSAP 缓动光斑：实元素以便动画 x/y） */
.glow-orb {
  position: absolute;
  top: -180px;
  right: -120px;
  width: 480px;
  height: 480px;
  border-radius: 50%;
  background: radial-gradient(
    circle,
    color-mix(in srgb, var(--am-blue-600) 35%, transparent),
    transparent 65%
  );
  pointer-events: none;
  will-change: transform;
}

.brand-top {
  display: flex;
  align-items: center;
  gap: 12px;
  position: relative;
}
.brand-mark {
  width: 40px;
  height: 40px;
  border-radius: 11px;
  background: linear-gradient(135deg, var(--am-blue-600), var(--am-blue-400));
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 18px color-mix(in srgb, var(--am-blue-600) 45%, transparent);
}
.brand-mark svg {
  width: 26px;
  height: 26px;
}
.brand-name {
  font-size: 19px;
  font-weight: 700;
  letter-spacing: 0.01em;
}

.brand-hero {
  position: relative;
  max-width: 460px;
}
.hero-title {
  font-size: 42px;
  line-height: 1.22;
  font-weight: 700;
  letter-spacing: 0.01em;
  margin: 0 0 14px;
}
.hero-sub {
  font-size: 15px;
  color: var(--am-blue-300);
  margin: 0 0 22px;
  letter-spacing: 0.02em;
  text-wrap: balance; /* 窄面板换行时避免尾字孤行 */
}

/* 迷你客服对话预览：白卡 + 蓝白光晕，示意产品形态 */
.hero-mini {
  max-width: 400px;
  margin: 0 0 24px;
  padding: 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 18px 42px color-mix(in srgb, var(--am-ink-950) 45%, transparent);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mini-ai-row {
  display: flex;
  align-items: flex-start;
  gap: 7px;
}
.mini-ai-dot {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  margin-top: 3px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--am-blue-600), var(--am-blue-400));
  box-shadow: 0 2px 6px color-mix(in srgb, var(--am-blue-600) 45%, transparent);
}
.mini-ai-bubble,
.mini-user-bubble {
  font-size: 12px;
  line-height: 1.55;
  padding: 6px 10px;
  border-radius: 10px;
  word-break: break-word;
}
.mini-ai-bubble {
  background: var(--am-blue-50);
  border: 1px solid var(--am-line);
  color: var(--am-text-2);
  border-top-left-radius: 3px;
}
.mini-user-row {
  display: flex;
  justify-content: flex-end;
}
.mini-user-bubble {
  background: var(--am-blue-600);
  color: #fff;
  border-top-right-radius: 3px;
  box-shadow: 0 3px 10px color-mix(in srgb, var(--am-blue-600) 35%, transparent);
}
.mini-input {
  margin-top: 2px;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--am-blue-200);
  border-radius: 8px;
}
.mini-input-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--am-blue-400);
}
.mini-input em {
  font-style: normal;
  font-size: 11px;
  color: var(--am-text-3);
}

.feature-list {
  list-style: none;
  display: flex;
  flex-direction: column;
}
.feature-list li {
  display: flex;
  align-items: baseline;
  gap: 14px;
  padding: 10px 0;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}
.feature-list li:last-child {
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}
.feature-idx {
  font-family: var(--am-font-mono);
  font-size: 12px;
  color: var(--am-blue-400);
  flex-shrink: 0;
}
.feature-name {
  font-size: 14px;
  font-weight: 600;
  flex-shrink: 0;
  width: 72px;
}
.feature-desc {
  font-size: 13px;
  color: var(--am-blue-300);
}

.brand-foot {
  position: relative;
  font-size: 11px;
  color: var(--am-blue-300); /* 原 #56688e 在墨蓝底上仅 2.9:1，提至 AA 达标的令牌色 */
  letter-spacing: 0.06em;
}

/* ---------- 右：表单面板 ---------- */
.form-panel {
  flex: 1;
  min-width: 420px;
  display: flex;
  overflow-y: auto;
  background: var(--am-card);
  padding: 40px;
}
.form-box {
  width: 100%;
  max-width: 360px;
  /* margin:auto 弹性居中：视口过矮（手机键盘弹起）时自动退化为顶对齐+面板内滚动，避免 flex 居中截断 */
  margin: auto;
}

.form-title {
  font-size: 26px;
  font-weight: 700;
  color: var(--am-text);
  margin: 0 0 6px;
}
.form-sub {
  font-size: 14px;
  color: var(--am-text-3);
  margin: 0 0 30px;
}

.login-btn {
  width: 100%;
  font-weight: 600;
  letter-spacing: 0.08em;
}

/* 输入框聚焦光晕：保留细描边，外包一层品牌蓝柔光 */
.form-box :deep(.el-input__wrapper) {
  border-radius: 8px;
  transition: box-shadow 0.2s ease;
}
.form-box :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px var(--el-color-primary) inset, 0 0 0 4px var(--am-glow-ring);
}

/* 演示账号：胶囊快速填入 */
.demo-accounts {
  margin-top: 28px;
  border: 1px solid var(--am-line);
  border-radius: var(--am-radius-lg);
  padding: 14px 16px;
  background: var(--am-blue-50);
}
.demo-caption {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.12em;
  color: var(--am-text-3);
  margin-bottom: 10px;
}
/* 胶囊排列：2 × 2 流式 */
.demo-chips {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.demo-chip {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid color-mix(in srgb, var(--am-blue-600) 14%, transparent);
  background: rgba(255, 255, 255, 0.6);
  cursor: pointer;
  transition: all 0.16s ease;
}
.demo-chip:hover,
.demo-chip:focus-visible {
  background: color-mix(in srgb, var(--am-blue-600) 9%, transparent);
  border-color: var(--am-blue-200);
}
/* focus-visible 不抵消全局焦点环（P1-3），键盘导航在胶囊上可见 */
.demo-role {
  font-size: 13px;
  font-weight: 600;
  color: var(--am-blue-600);
  flex-shrink: 0;
  transition: color 0.16s ease;
}
.demo-chip:hover .demo-role,
.demo-chip:focus-visible .demo-role {
  color: var(--am-blue-500);
}
.demo-cred {
  font-size: 12px;
  color: var(--am-text-3);
  transition: color 0.16s ease;
}
.demo-chip:hover .demo-cred,
.demo-chip:focus-visible .demo-cred {
  color: var(--am-text-2);
}

/* ---------- 响应式：对齐全站断点体系（useBreakpoint：1024 / 768） ---------- */
/* ≤1280：品牌面板 40% 宽的内容区不足 400px，装不下 hero-mini 的设计宽度，隐藏迷你对话 */
@media (max-width: 1280px) {
  .hero-mini {
    display: none;
  }
}
/* 平板档（768-1023，对齐 isTablet）：品牌面板收窄为紧凑形态；
   feature 描述在 ~200px 内容区会换 3-4 行，只保留「编号 + 名称」 */
@media (max-width: 1023px) {
  .brand-panel {
    flex-basis: 36%;
    padding: 36px 32px;
  }
  .hero-title {
    font-size: 36px;
  }
  .hero-sub {
    margin-bottom: 28px;
  }
  .feature-desc {
    display: none;
  }
}
/* 手机档（<768，对齐 isMobile）：单栏登录——品牌面板隐藏，表单区取消最小宽并收窄内边距 */
@media (max-width: 767px) {
  .brand-panel {
    display: none;
  }
  .form-panel {
    min-width: 0;
    padding: 32px 24px;
  }
}
</style>
