# 登录页优化方案（蓝白 · 工程蓝图风）

> 范围：`frontend/index.html`、`frontend/src/styles/main.css`、`frontend/src/views/Login/index.vue`（连带全站设计令牌体系）
> 状态：已实施并验证（49/49 单测通过，Playwright computed styles + 几何校验通过）

---

## 1. 背景与目标

### 1.1 问题审计（改造前）

改造前登录页是典型的「AI 生成模板脸」：

| 问题 | 原实现 |
|---|---|
| 紫色渐变背景 | `linear-gradient(135deg, #667eea 0%, #764ba2 100%)` —— AI 生成界面最泛滥的配色 |
| 居中卡片布局 | 一张 `el-card` 悬浮在渐变上，无品牌叙事 |
| 默认 Element 蓝 | `#409eff` + `#1890ff` 混用，未做主题定制 |
| 系统字体 | `-apple-system, ...` 无品牌识别度 |
| 测试账号提示 | 一条 `el-alert` 塞三行文字，无交互 |

### 1.2 目标

- **蓝白配色**：以墨蓝/钴蓝为主、云白为底，摆脱紫色渐变与默认蓝
- **反 AI 模板化**：布局、字体、纹理有明确设计主张，而非通用组件默认值
- **演示友好**：演示账号可点击快速填入，角色名称用蓝色链接样式明确可点感

## 2. 设计方法论来源

参考两个开源项目（均为方法论采纳，非依赖引入）：

| 项目 | 借鉴点 |
|---|---|
| [nexu-io/open-design](https://github.com/nexu-io/open-design) | 「设计系统即品牌契约」：设计令牌集中在 DESIGN.md / CSS 变量中定义，全站一致消费，拒绝散落硬编码 |
| [leonxlnx/taste-skill](https://github.com/leonxlnx/taste-skill) | 反模板化原则（anti-slop）：拒绝紫色渐变/默认模板脸；排版优先（distinctive display font + refined body font）；单一主色克制用色；其 `redesign-existing-projects` 技能的「先审计再改版」流程即本次实施流程 |

## 3. 设计令牌体系（main.css）

所有颜色通过 `--am-*` CSS 变量定义，页面样式一律消费令牌：

### 3.1 品牌色阶（墨蓝 → 钴蓝 → 浅蓝）

```css
--am-ink-950: #081426;   /* 最深墨蓝（侧边栏/品牌面板底部） */
--am-ink-900: #0e2140;   /* 墨蓝（侧边栏/品牌面板主底色） */
--am-ink-800: #152d55;   /* 深蓝（头像底） */
--am-blue-600: #2352c5;  /* 钴蓝主色（链接/按钮/激活态） */
--am-blue-500: #3565d9;  /* 钴蓝亮（hover） */
--am-blue-400: #5b84e6;  /* 浅钴蓝（品牌 SVG 节点） */
--am-blue-50:   #edf2fc; /* 浅蓝底（卡片内衬/表格头） */
```

### 3.2 中性色（蓝灰调，非纯灰）

```css
--am-paper: #f5f7fb;     /* 纸白（页面底色） */
--am-line: #e2e8f2;      /* 描边 */
--am-text: #1b2a41;      /* 主文字 */
--am-text-3: #8896ac;    /* 弱文字 */
```

### 3.3 字体

- **Sora**（Google Fonts）：显示/正文字体，几何感强、非 Inter/Roboto 常见脸
- **JetBrains Mono**：等宽字体，用于账号密码、技术标识（`RAG · MCP · LangGraph`）
- 通过 `--el-font-family` 覆盖 Element Plus 全局字体

### 3.4 Element Plus 主题覆盖

```css
--el-color-primary: #2352c5;          /* 替换默认 #409eff */
--el-color-primary-light-3 ~ 9: …;    /* 同步浅色阶 */
--el-border-radius-base: 6px;
```

## 4. 登录页布局方案

采用**分屏布局**（split-screen）替代居中卡片：

```
┌──────────────────────┬──────────────────┐
│  品牌面板（flex 1.15） │  表单面板（flex 1） │
│  深墨蓝渐变 + 点阵纹理  │  云白 #ffffff      │
│                      │                  │
│  ● AssistMind        │  欢迎回来          │
│                      │  登录后进入工作台    │
│  让文档               │                  │
│  开口回答              │  [用户名输入框]     │
│  SaaS 产品文档智能问答   │  [密码输入框]      │
│                      │  [  登 录  ]      │
│  01 混合检索  向量+BM25 │                  │
│  02 工具编排  MCP…     │  演示账号·点击填入  │
│  03 根因诊断  证据链…   │  管理员 admin/…   │
│                      │  客服   agent/…   │
│  RAG·MCP·LangGraph   │  用户   user/…    │
└──────────────────────┴──────────────────┘
```

### 4.1 品牌面板（左）

| 元素 | 设计 |
|---|---|
| 背景 | 墨蓝渐变 `160deg #0e2140 → #081426` + 蓝图点阵纹理（`radial-gradient` 22px 网格）+ 右上角钴蓝柔光 |
| 品牌标识 | 圆角方块 + 知识图谱 SVG（三节点：蓝顶点 + 白双底点 + 白连线），渐变 `#2352c5 → #5b84e6` |
| 主标语 | 「让文档开口回答」46px 大字排版，分行断句 |
| 特性列表 | 编号式规格表（`01/02/03` 等宽字体编号 + 特性名 + 描述），上下细分隔线，工程蓝图气质 |
| 页脚 | `RAG · MCP · LangGraph` 等宽字体技术标识 |

### 4.2 表单面板（右）

- 云白背景，表单区最大宽度 360px 居中
- 标题「欢迎回来」+ 副标题，与左面板大标语形成层级呼应
- 登录按钮满宽、钴蓝、字距加宽（`letter-spacing: 0.08em`）
- 演示账号区（见第 5 节交互设计）

### 4.3 响应式

`max-width: 860px` 时隐藏品牌面板，表单面板独占——小屏只保留核心登录功能。

## 5. 交互设计：演示账号快速填入

### 5.1 行为

- 数据驱动：`DEMO_ACCOUNTS` 数组（label/username/password）`v-for` 渲染
- 点击整行 → `fillAccount(acc)` 填入表单用户名+密码
- 键盘可达：`role="button"` + `tabindex="0"` + Enter 触发（无障碍）

### 5.2 蓝色链接样式（可点击感）

| 状态 | 角色名称 | 行为反馈 |
|---|---|---|
| 默认 | 钴蓝 `#2352c5`、500 字重 | 链接可点感 |
| 悬浮/聚焦 | 亮钴蓝 `#3565d9` + 下划线（offset 3px） | 链接反馈 |
| 行热区 | 整行浅蓝底 `rgba(35,82,197,0.09)` + 圆角 + `cursor: pointer` | 扩大点击区域 |
| 账号密码 | 灰色等宽字体（悬浮微亮） | 与蓝色角色名形成视觉层级 |

### 5.3 代码结构

```js
const DEMO_ACCOUNTS = [
  { label: '管理员', username: 'admin', password: 'admin123' },
  { label: '客服',   username: 'agent', password: 'agent123' },
  { label: '用户',   username: 'user',  password: 'user123' },
]

function fillAccount(acc) {
  form.username = acc.username
  form.password = acc.password
}
```

## 6. 技术要点与踩坑

### 6.1 双层背景覆盖坑（已修复）

`.am-grid-dark` 点阵的 `background-image` 会**覆盖** `.brand-panel` 简写里的渐变，导致深蓝底丢失（computed `background-color` 变透明）。正确写法是双背景层：

```css
.brand-panel.am-grid-dark {
  background-image:
    radial-gradient(circle, rgba(91,132,230,0.16) 1px, transparent 1px), /* 点阵在上 */
    linear-gradient(160deg, var(--am-ink-900) 0%, var(--am-ink-950) 100%); /* 渐变在下 */
  background-size: 24px 24px, 100% 100%;
}
```

### 6.2 品牌图标

favicon 用内联 SVG data URI（三节点知识图谱），`index.html` 中直接声明，免额外文件；`theme-color: #0e2140` 统一移动端浏览器 chrome 色。

### 6.3 登录态注入（测试用）

登录态存于 `localStorage`（`assistmind_token` / `assistmind_user`），路由守卫只校验 token 存在性——Playwright 验证侧边栏时直接注入伪造 token 即可，无需起后端。

### 6.4 真实登录链路暴露的两个存量 bug（已修复）

E2E 真实登录测试（点击演示账号 → 登录 → 校验跳转）发现两个此前从未暴露的前后端契约/权限 bug：

**① 登录请求契约不匹配（登录必现失败）**

- 现象：任意账号登录都失败
- 根因：`frontend/src/api/auth.js` 把账号密码放在 **query params**（`post('/auth/login', null, { params: data })`），而后端 `LoginRequest`（Pydantic）要求 **JSON body**
- 修复：改为 `request.post('/auth/login', data)`
- 教训：只测 UI 交互（表单填充）不测真实链路，契约 bug 永远不会暴露

**② 侧边栏权限菜单泄漏（agent/user 可见管理员菜单）**

- 现象：agent 角色显示「管理后台」、user 角色显示「知识库 + 管理后台」
- 根因：`MainLayout.canAccess('admin')` 用小写名匹配路由，而路由名为大写 `'Admin'`——`router.getRoutes().find()` 落空返回 undefined，走了 `return true`（无权限限制）兜底
- 修复：大小写不敏感匹配 `String(x.name).toLowerCase() === String(name).toLowerCase()`
- 说明：路由守卫 `hasPermission` 仍拦截了实际页面访问，本 bug 只是菜单展示层泄漏，但会给用户错误的功能预期

## 7. 验证方案

无多模态读图能力时，用 **Playwright computed styles + 元素几何**做程序化验证：

| 验证项 | 实测值 | 结论 |
|---|---|---|
| 品牌面板背景 | `点阵radial-gradient + linear-gradient(160deg, #0e2140→#081426)` | ✓ |
| 表单面板 | `rgb(255,255,255)` | ✓ |
| 登录按钮 | `rgb(35,82,197)` = 钴蓝 | ✓ |
| 全局字体 | `Sora, …` | ✓ |
| 分屏比例 | 780px / 660px（1440 视口） | ✓ |
| 角色名默认色 | `rgb(35,82,197)` 钴蓝 | ✓ |
| 角色名悬浮 | 变亮 `rgb(53,101,217)` + `underline` | ✓ |
| 点击填充 | 三行分别填入 `admin/admin123`、`agent/agent123`、`user/user123` | ✓ |
| 单元测试 | 49/49 通过 | ✓ |

截图存档：`.trae/shots/login.png`、`login-fill.png`（含悬浮态）、`app-chat.png`、`sidebar.png`。

## 8. 相关文件

| 文件 | 角色 |
|---|---|
| `frontend/index.html` | 字体加载、favicon、theme-color |
| `frontend/src/styles/main.css` | 设计令牌 + Element Plus 变量覆盖（全站共用） |
| `frontend/src/views/Login/index.vue` | 登录页布局与交互 |

> 本方案的设计令牌同时支撑全站（侧边栏/顶栏/五页面）的蓝白改造，登录页是其中一组消费方；后续新增页面应直接消费 `--am-*` 令牌，禁止新增硬编码颜色（语义状态色除外：成功绿/警告橙/危险红）。
