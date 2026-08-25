<template>
  <div class="login-page">
    <!-- 左：品牌面板（深墨蓝 + 蓝图点阵） -->
    <div class="brand-panel am-grid-dark">
      <div class="brand-top">
        <div class="brand-mark">
          <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="10" cy="22" r="3" fill="#fff" />
            <circle cx="22" cy="22" r="3" fill="#fff" />
            <circle cx="16" cy="9" r="3" fill="#5b84e6" />
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
            <el-input v-model="form.username" placeholder="用户名" :prefix-icon="User" size="large" />
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
          <div
            v-for="acc in DEMO_ACCOUNTS"
            :key="acc.username"
            class="demo-row"
            role="button"
            tabindex="0"
            @click="fillAccount(acc)"
            @keydown.enter.prevent="fillAccount(acc)"
          >
            <span class="demo-role">{{ acc.label }}</span>
            <span class="am-mono demo-cred">{{ acc.username }} / {{ acc.password }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { User, Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const router = useRouter()
const auth = useAuthStore()
const formRef = ref()
const loading = ref(false)

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
      rgba(91, 132, 230, 0.16) 1px,
      transparent 1px
    ),
    linear-gradient(160deg, var(--am-ink-900) 0%, var(--am-ink-950) 100%);
  background-size: 24px 24px, 100% 100%;
}
/* 顶部柔光 */
.brand-panel::before {
  content: '';
  position: absolute;
  top: -180px;
  right: -120px;
  width: 480px;
  height: 480px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(35, 82, 197, 0.35), transparent 65%);
  pointer-events: none;
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
  box-shadow: 0 6px 18px rgba(35, 82, 197, 0.45);
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
  font-size: 46px;
  line-height: 1.22;
  font-weight: 700;
  letter-spacing: 0.01em;
  margin: 0 0 14px;
}
.hero-sub {
  font-size: 15px;
  color: #9db0d4;
  margin: 0 0 40px;
  letter-spacing: 0.02em;
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
  padding: 13px 0;
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
  color: #8ba0c6;
}

.brand-foot {
  position: relative;
  font-size: 11px;
  color: #56688e;
  letter-spacing: 0.06em;
}

/* ---------- 右：表单面板 ---------- */
.form-panel {
  flex: 1;
  min-width: 420px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--am-card);
  padding: 40px;
}
.form-box {
  width: 100%;
  max-width: 360px;
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

/* 演示账号：规格表样式 */
.demo-accounts {
  margin-top: 28px;
  border: 1px solid var(--am-line);
  border-radius: 10px;
  padding: 14px 16px;
  background: var(--am-blue-50);
}
.demo-caption {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.12em;
  color: var(--am-text-3);
  margin-bottom: 8px;
}
.demo-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 10px;
  margin: 0 -10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.16s ease;
}
.demo-row:hover,
.demo-row:focus-visible {
  background: rgba(35, 82, 197, 0.09);
  outline: none;
}
.demo-role {
  font-size: 13px;
  font-weight: 500;
  color: var(--am-blue-600);
  flex-shrink: 0;
  transition: color 0.16s ease;
}
.demo-row:hover .demo-role,
.demo-row:focus-visible .demo-role {
  color: var(--am-blue-500);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.demo-cred {
  font-size: 12px;
  color: var(--am-text-3);
  transition: color 0.16s ease;
}
.demo-row:hover .demo-cred,
.demo-row:focus-visible .demo-cred {
  color: var(--am-text-2);
}

/* ---------- 响应式：中屏收窄品牌面板 ---------- */
@media (max-width: 1080px) {
  .brand-panel {
    flex-basis: 36%;
    padding: 36px 40px;
  }
  .hero-title {
    font-size: 36px;
  }
  .hero-sub {
    margin-bottom: 28px;
  }
  .feature-list li {
    padding: 10px 0;
  }
}

/* ---------- 响应式：窄屏隐藏品牌面板 ---------- */
@media (max-width: 860px) {
  .brand-panel {
    display: none;
  }
  .form-panel {
    min-width: 0;
  }
}
</style>
