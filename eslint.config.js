import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'

// 仓库级 ESLint（flat config）。
// 范围：仅前端源码与测试；后端由 ruff 负责，见 backend/pyproject.toml。
// 规则集刻意保守：只启用「能抓到真实缺陷」的规则，格式类规则交给 Prettier。
export default [
  {
    ignores: [
      '**/node_modules/**',
      '**/dist/**',
      'frontend/coverage/**',
      'backend/**',
      'private-notes/**',
    ],
  },
  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  {
    files: ['frontend/**/*.{js,vue}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      // 未使用变量是错误；下划线前缀显式表示「有意忽略」
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      // 页面级组件按目录命名（views/Chat/index.vue），不强制多词
      'vue/multi-word-component-names': 'off',
      // 空 catch 必须写明降级意图，避免静默吞错（与后端 except 降级三要素对齐）
      'no-empty': ['error', { allowEmptyCatch: false }],
    },
  },
  {
    files: ['frontend/tests/**/*.js', 'frontend/**/*.spec.js'],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
        describe: 'readonly',
        it: 'readonly',
        test: 'readonly',
        expect: 'readonly',
        vi: 'readonly',
        beforeAll: 'readonly',
        afterAll: 'readonly',
        beforeEach: 'readonly',
        afterEach: 'readonly',
      },
    },
  },
  {
    files: ['*.config.js', 'frontend/vite.config.js'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
  {
    // docs/ 下的本地脚本：CommonJS + Playwright（page.evaluate 内是浏览器上下文）
    files: ['**/*.cjs'],
    languageOptions: {
      sourceType: 'commonjs',
      globals: { ...globals.node, ...globals.browser },
    },
  },
]
