# AssistMind 前端设计改进 Spec（P0 / P1 / P2）

> 版本：v1.0 ｜ 日期：2026-09-05
> 依据：2026-09-05 前端设计评审（main.css / MainLayout / Chat / AiInputBox / 各视图样式扫描）
> 范围：仅前端 `frontend/`，不动后端接口与业务逻辑；不改 AGENTS.md 既定架构约束
> 约束：JavaScript（禁 TypeScript）、Element Plus 优先、Pinia 状态管理

---

## 0. 背景与目标

当前桌面端视觉体系成熟（设计令牌完整、蓝白工程蓝图风一致），但响应式是最大短板（评审 3/10）：主框架零媒体查询、侧边栏固定 232px、抽屉固定 380px、表格页无移动端适配。另有对比度、暗色模式不一致、触控目标偏小、流式强制滚底等问题。

目标：

- P0：≤768px 移动端可用（无横向溢出、导航可达、表格可横向滚动查看）
- P1：一致性与可访问性达标（对比度 ≥ WCAG AA 4.5:1、键盘焦点可见、滚动行为不打扰）
- P2：打磨（Markdown 渲染完整、令牌补齐、字体去 CDN 依赖）

验收基线：Chrome DevTools 设备模拟 iPhone SE(375×667) / iPad(768×1024) / 桌面(1440×900) 三档人工走查 + `npm run test:unit` 不回归。

---

## P0-1 主框架响应式侧边栏

**现状**：`MainLayout.vue` `<el-aside width="232px">` 固定，全文件无任何媒体查询；<1024px 时 Chat 页（860px 对话流 + 232px 侧栏 + 边距）必然横向溢出。

**目标**：

| 断点 | 行为 |
|---|---|
| ≥1024px | 现状不变，侧栏 232px 完整展开 |
| 768–1023px | 侧栏折叠为 64px 图标栏（el-menu collapse 动画） |
| <768px | 侧栏脱离文档流，改为 `el-drawer` 抽屉式导航；顶栏左侧出现汉堡按钮 |

**实现要点**（`frontend/src/components/Layout/MainLayout.vue`）：

1. 新增响应式状态（建议抽为 `src/utils/useBreakpoint.js`，供 P0-2/P0-3 复用）：

```js
// src/utils/useBreakpoint.js
import { ref, onMounted, onBeforeUnmount } from 'vue'

export function useBreakpoint() {
  const width = ref(window.innerWidth)
  const onResize = () => { width.value = window.innerWidth }
  onMounted(() => window.addEventListener('resize', onResize))
  onBeforeUnmount(() => window.removeEventListener('resize', onResize))
  return {
    width,
    isTablet: () => width.value < 1024,   // computed 包装
    isMobile: () => width.value < 768,
  }
}
```

2. 模板改造：
   - `<el-aside :width="sidebarWidth">`，`sidebarWidth` = mobile 时 '0' / tablet 时 '64px' / 桌面 '232px'
   - `<el-menu :collapse="isTablet && !isMobile" :collapse-transition="false">`
   - mobile 时侧栏内容整体移入 `<el-drawer v-model="navDrawerOpen" direction="ltr" size="232px" :with-header="false">`，菜单项点击后关闭抽屉
   - 顶栏 `.header-left` 前插入汉堡按钮（`v-if="isMobile"`，`<el-icon><Expand/></el-icon>` 或 Fold/Expand 切换），点击打开抽屉
   - 品牌区折叠态只显示 `.brand-mark`（`.brand-text` 在 collapse 时 `display:none`）；`.sidebar-foot`、`.nav-caption` 折叠态隐藏

3. 样式：媒体查询写在组件 `<style scoped>` 内，断点统一用 1024px / 768px（与 Login 页既有 860px 断点不冲突，Login 是独立全屏页）。

**注意**：
- el-badge 包裹工单菜单项的 `.ticket-badge { display: block }` 在 collapse 态需确认徽标位置不偏移（折叠时图标居中，badge 默认右上角即可）。
- keep-alive/transition 逻辑不动。

**验收**：
- 1023px：侧栏 64px，菜单仅图标，hover 弹出文字（el-menu collapse 自带）
- 767px：无侧栏占位，汉堡按钮可开合抽屉，点菜单项跳转后抽屉自动关闭
- 三档宽度下页面无横向滚动条

---

## P0-2 历史会话抽屉宽度自适应

**现状**：`Chat/index.vue` 历史会话 `el-drawer size="380px"` 固定，比 375px 宽的手机屏幕还宽，直接溢出。

**实现**：

```html
<el-drawer :size="isMobile ? '100%' : '380px'" ...>
```

`isMobile` 复用 P0-1 的 `useBreakpoint()`。

**验收**：iPhone SE 模拟下抽屉全屏打开，会话项「查看 / 继续对话」按钮可点、不截断。

---

## P0-3 表格页横向滚动与筛选区折行

**现状**：Tickets / Orders / Admin / Knowledge 的 el-table 列 min-width 累加（Tickets 180+180+…、Admin 工单号 220），窄屏撑破容器；筛选区固定宽度下拉（内联 `style="width: 180px"` 等）不折行。

**实现**：

1. 各表格页最外层已用/补用 `.am-table-wrap`（main.css 已有该类），在其内给 el-table 容器加：

```css
.table-scroll { overflow-x: auto; }
.table-scroll .el-table { min-width: 720px; } /* 按各页列宽总和微调 */
```

2. 筛选工具条统一改为：

```css
.filter-bar { display: flex; flex-wrap: wrap; gap: 8px; }
```

移除内联固定宽度或改为 `style="width: 160px"` 以内并允许折行；<768px 时筛选项 `width: 100%`（媒体查询）。

3. Admin 页统计卡 `grid-template-columns: 1fr 1fr`（Admin/index.vue:1072 附近）补：

```css
@media (max-width: 768px) {
  .stat-grid { grid-template-columns: 1fr; }
}
```

**涉及文件**：`views/Tickets/index.vue`、`views/Orders/index.vue`、`views/Admin/index.vue`、`views/Knowledge/index.vue`

**验收**：375px 宽度下各表格页 body 无横向滚动（表格容器内部可横滑），筛选控件纵向堆叠不错位。

---

## P1-1 文本对比度达标（WCAG AA）

**现状**：`--am-text-3: #8896ac` 在 `--am-paper: #f5f7fb` 上对比度约 3:1，低于 AA 4.5:1；它被用于 placeholder、meta 文本、`.ai-hint`、`.page-path`；侧边栏 `.nav-caption #5a6c92` 在 ink-900 上约 3.5:1。

**实现**（`styles/main.css`）：

```css
--am-text-3: #6b7a90;  /* 在 #f5f7fb 上 ≈ 4.6:1 */
```

`MainLayout.vue` 内 `.nav-caption` 调亮：

```css
color: #8fa0c2;  /* 深墨蓝底上 ≥ 4.5:1，实测定稿 */
```

**注意**：`--am-text-3` 全站引用点多，改后需目检 Login/Chat/Admin 三页确认不发闷；纯装饰性分隔符（`.page-path-sep`）不受 AA 约束，可同时改用 `--am-text-3` 修复"几乎看不见"问题（P2 关联）。

**验收**：Chrome DevTools Lighthouse Accessibility 审计无 "contrast" 警告（正文级文本）。

---

## P1-2 色彩体系统一：移除孤岛暗色 + 硬编码色令牌化

**现状 A**：`AiInputBox.vue:386` 单独响应 `prefers-color-scheme: dark`，而全站无暗色——暗色系统下输入框变黑、页面是白色，视觉断裂。

**实现 A（短期）**：删除 `AiInputBox.vue` 中整个 `@media (prefers-color-scheme: dark)` 块；`--ai-*` 令牌保留亮色值。（全站暗色为远期独立需求，不在本 spec。）

**现状 B**：Chat 页类型色条与诊断 chip 使用硬编码 hex（`#14b8a6`、`#6366f1`、`#f43f5e`、`#047857`、`#6d28d9`、`#b45309` 等）。

**实现 B**：`main.css` 增补语义令牌：

```css
--am-teal-600: #0d9488;
--am-indigo-600: #4f46e5;
--am-rose-600: #e11d48;
--am-green-700: #047857;
--am-violet-700: #6d28d9;
--am-amber-700: #b45309;
```

Chat/index.vue 中对应硬编码值替换为 `var(--am-*)`。

**验收**：`grep -n "#[0-9a-fA-F]\{6\}" frontend/src/views/Chat/index.vue` 中类型色条/chip 相关行清零（品牌渐变等已有令牌的除外）。

---

## P1-3 键盘焦点可见 + 触控目标放大

**现状**：自绘按钮（`.fb-btn`、`.diag-header`、`.ai-icobtn`、`.ai-send`、`.copy-json`）只有 hover 态，无 `:focus-visible`；反馈按钮 26px、图标按钮 32px，低于 44px 触控建议。

**实现**：

1. `main.css` 全局补焦点环：

```css
:focus-visible {
  outline: 2px solid var(--am-blue-500);
  outline-offset: 2px;
  border-radius: 4px;
}
```

（验证不与 Element 自带 focus 样式叠加冲突；冲突则限定到自定义按钮类。）

2. 触控目标：<768px 下放大（不改桌面密度）：

```css
@media (max-width: 768px) {
  .fb-btn { width: 40px; height: 40px; }
  .ai-icobtn, .ai-send { width: 40px; height: 40px; }
}
```

桌面端保持现状。

**验收**：Tab 键走查 Chat 页所有可交互元素均有可见焦点环；375px 下主要按钮触控面积 ≥ 40×40。

---

## P1-4 Chat 流式滚动改为"贴底才跟随"

**现状**：`watch(messages, scrollToBottom, { deep: true })` 在流式期间每次 delta 都强制滚底，用户上翻阅读历史时会被拽回底部。

**实现**（`Chat/index.vue`）：

1. 新增 `isAtBottom = ref(true)`；消息列表 `listRef` 绑 `@scroll`：

```js
function onListScroll() {
  const el = listRef.value
  if (!el) return
  isAtBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}
```

2. watch 回调改条件滚动：

```js
watch(messages, () => { if (isAtBottom.value) scrollToBottom() }, { deep: true })
```

3. 用户发送新消息（`send()` 成功推入用户气泡后）无条件 `scrollToBottom()`——自己发言必然回到底部。

**验收**：流式输出中上翻 200px，页面停留不动；滚回底部附近（<40px）恢复跟随；发送新消息立即回底。

---

## P2-1 md-body 补齐 Markdown 元素样式

**现状**：`Chat/index.vue` `.md-body` 只样式化了 p / pre / code / ul / ol；LLM 输出表格、标题、引用块会裸奔（无框线、无层级）。

**实现**（`.md-body` 作用域内追加）：

```css
.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3), .md-body :deep(h4) {
  margin: 0.8em 0 0.4em; font-weight: 600; line-height: 1.4; color: var(--am-text);
}
.md-body :deep(h1) { font-size: 1.15em; }
.md-body :deep(h2) { font-size: 1.08em; }
.md-body :deep(h3), .md-body :deep(h4) { font-size: 1em; }
.md-body :deep(table) {
  border-collapse: collapse; margin: 0.6em 0; font-size: 0.92em;
  display: block; overflow-x: auto;  /* 窄气泡内可横滑 */
}
.md-body :deep(th), .md-body :deep(td) {
  border: 1px solid var(--am-line); padding: 5px 10px; text-align: left; white-space: nowrap;
}
.md-body :deep(th) { background: var(--am-blue-50); font-weight: 600; }
.md-body :deep(blockquote) {
  margin: 0.6em 0; padding: 4px 12px; border-left: 3px solid var(--am-blue-200);
  color: var(--am-text-2);
}
```

**验收**：让客服输出含 `## 标题` + 表格 + 引用的回答（可用内置 mock 或直接构造消息），气泡内渲染有层级、表格可横滑不破气泡宽度。

---

## P2-2 细节修正包

| # | 位置 | 现状 | 改法 |
|---|---|---|---|
| a | `MainLayout.vue:255` | 使用未定义的 `--am-blue-300`，靠 `var(..., fallback)` 兜住 | `main.css` 补 `--am-blue-300: #8ca6e5;` |
| b | `MainLayout.vue:324` | `.page-path-sep` 用边框色 `--am-line` 当文字色 | 改 `color: var(--am-text-3)`（随 P1-1 调深后可见） |
| c | `Chat/index.vue` `.card-header` | `flex-wrap` 后 gap 仅 4px，按钮组拥挤 | gap 改 `8px 12px` |
| d | `Chat/index.vue:1841` | quick-btn 用 `!important` 压 Element 默认样式 | 提高选择器特异性（`.quick-row :deep(.el-button.quick-btn)`）去掉 `!important` |
| e | `MainLayout.vue:267` | 选中项辉光 `rgba(35,82,197,0.4)` 偏重 | 降至 `0.25` |

---

## P2-3 字体去 Google Fonts CDN 依赖

**现状**：`index.html` 从 fonts.googleapis.com 加载 Sora + JetBrains Mono，国内演示环境有加载慢/失败风险（FOUT/回退跳变）。

**实现（二选一，推荐方案 A）**：

- **方案 A 自托管**：下载 Sora（400/500/600/700 woff2，latin 子集）与 JetBrains Mono（400/600 woff2）至 `frontend/public/fonts/`，`main.css` 顶部加 `@font-face`（`font-display: swap`），删除 `index.html` 的 fonts.googleapis 两个 `<link>`。
- **方案 B 系统栈优先**：`--am-font-sans` 改为系统字体在前、Sora 兜底：`-apple-system, 'PingFang SC', 'Microsoft YaHei', 'Sora', ...`，同样删除 CDN link（牺牲品牌字体一致性，换零依赖）。

**验收**：断网/屏蔽 Google Fonts 下首屏无字体加载阻塞，无明显 FOUT 跳变。

---

## 实施顺序与影响面

| 阶段 | 条目 | 涉及文件 | 预估改动量 |
|---|---|---|---|
| 第一批 P0 | P0-1 / P0-2 / P0-3 | MainLayout.vue、新增 useBreakpoint.js、Chat/index.vue、四个表格页 | 中 |
| 第二批 P1 | P1-1 → P1-2 → P1-3 → P1-4 | main.css、AiInputBox.vue、Chat/index.vue、MainLayout.vue | 小-中 |
| 第三批 P2 | P2-1 / P2-2 / P2-3 | Chat/index.vue、main.css、MainLayout.vue、index.html、public/fonts/ | 小 |

回归检查：每批完成后 `cd frontend && npm run test:unit`，并三档宽度人工走查五个页面。

**明确不做**：全站暗色模式（独立需求另行评估）；Login 页改动（本次未获读取权限，其已有三档断点，移动端表现待单独评审）；后端任何接口。
