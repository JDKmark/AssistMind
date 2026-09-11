import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 大依赖 vendor 分包（路由级动态导入已在 router/index.js 就位）：
        // - element-plus：main.js 全局全量注册，无法 tree-shake，独立成块（gzip ~260kB）
        //   注：禁止改用 unplugin 按需导入（v-permission 指令与动态组件依赖全局注册）
        // - md-render：Chat 的 markdown 渲染链，仅 Chat 路由加载
        // - gsap：仅 Login 使用
        // - vendor：vue/pinia/axios 等运行时基座，入口即需
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('element-plus') || id.includes('@element-plus') || id.includes('dayjs')) {
            return 'element-plus'
          }
          if (
            id.includes('markdown-it') ||
            id.includes('dompurify') ||
            id.includes('entities') ||
            id.includes('linkify-it') ||
            id.includes('mdurl') ||
            id.includes('uc.micro') ||
            id.includes('punycode')
          ) {
            return 'md-render'
          }
          if (id.includes('gsap')) {
            return 'gsap'
          }
          return 'vendor'
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // 后端端口与 backend/.env 保持一致（8002；8001 曾被本机进程占用可被覆盖）
        target: process.env.VITE_API_PROXY_TARGET || 'http://localhost:8002',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
})
