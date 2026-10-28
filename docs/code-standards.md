# AssistMind 代码规范

> 适用范围：`backend/`、`frontend/`、`knowledge/`、`scripts/`、`deploy/` 全部代码。
> 与 `AGENTS.md` 的关系：`AGENTS.md` 面向 AI 编码工具，写「容易猜错的规则」；本文面向人类开发者，写「怎么写、为什么这么写、违反后怎么判定」。
> 两者冲突时以 `AGENTS.md` 的架构约束为准，并回改本文。
> 最后更新：2026-10-28

---

## 0. 效力分级

规范条目按违反后果分三级，Review 时据此判定阻塞与否（详见 `docs/code-review-checklist.md`）：

| 级别 | 含义 | 典型例子 | 处理 |
|---|---|---|---|
| **P0 红线** | 破坏架构契约、安全边界或数据正确性 | 绕过 `llm_profile()` 直读 `settings.DEEPSEEK_*`；`except: pass`；跨角色命中语义缓存；SSE 改 WebSocket | 必须改，阻塞合并 |
| **P1 规范** | 影响可维护性、可观测性、可测试性 | 降级路径无 `logger.warning`；新增功能无测试；硬编码端口/URL | 必须改或登记技术债并说明理由 |
| **P2 风格** | 一致性与可读性 | 命名、行宽、docstring、注释语气 | 建议改，不阻塞 |

「登记技术债」指：在 PR 描述里写明「欠什么、为什么现在不做、谁在什么条件下补」，不允许静默放过。

---

## 1. 工具链与门禁

### 1.1 已生效的配置（不要另起一套）

| 项 | 位置 | 现状 |
|---|---|---|
| 行宽 / 目标版本 | `backend/pyproject.toml` `[tool.ruff]` | `line-length = 100`、`target-version = "py311"` |
| Lint 规则集 | `[tool.ruff.lint] select` | `E,F,W,I,N,UP,B,A,C4,SIM` |
| 测试 | `[tool.pytest.ini_options]` | `asyncio_mode = "auto"`、`addopts = "-q"`、markers：`integration` / `requests_smoke` |
| 覆盖率门禁 | CI only | `--cov=app --cov-fail-under=70`；**本地 `addopts` 不带 `--cov`，不要加回** |
| 前端测试 | `frontend/vite.config.js` `test` | vitest + jsdom + `globals: true`，用例在 `frontend/tests/unit/*.spec.js` |
| 前端 lint | 仓库根 `eslint.config.js` | ESLint 9 flat config：`@eslint/js` recommended + `eslint-plugin-vue` **essential**；格式类规则交给 Prettier |
| 前端格式 | 仓库根 `.prettierrc.json` | `semi: false`、`singleQuote: true`、`printWidth: 100`、`endOfLine: lf`；`knowledge/`、`docs/`、`*.md`、`backend/` 不参与格式化 |
| 提交钩子 | `.husky/pre-commit`、`.husky/pre-push` | pre-commit：lint-staged（仅暂存文件）；pre-push：后端 + 前端单测 |

> 仓库根 `package.json` 只放工程化依赖（eslint / prettier / husky / lint-staged），业务依赖仍在 `frontend/package.json`。
> 首次克隆后需在**仓库根**执行一次 `npm install`（`prepare` 会安装 Git 钩子），否则 pre-commit 会跳过并提示。

### 1.2 本地必跑命令

```powershell
# 后端：格式与静态检查（提交前）
cd backend; venv\Scripts\python.exe -m ruff check app tests scripts
cd backend; venv\Scripts\python.exe -m ruff check --fix app tests scripts

# 后端：单元测试（不需要 Docker）
cd backend; venv\Scripts\python.exe -m pytest tests -q -k "not integration"

# 前端：单测
cd frontend; npm run test:unit

# 前端：lint / 格式化（仓库根）
npm run lint
npm run lint:fix
npm run format:check
```

### 1.3 门禁现状（2026-10-28 已收敛）

三项历史缺口已修复，Reviewer 可以依赖自动化拦截：

1. **CI lint 已阻断**：`.github/workflows/ci.yml` 的 ruff 步骤去掉 `--exit-zero`，存量 117 条告警已清零；前端 job 增加 `eslint .` 与 `prettier --check .` 两步，均为阻断。
2. **前端已有 ESLint / Prettier 配置**：`lint-staged` 真正生效（ESLint `--fix` + Prettier `--write`），并且全仓库格式化基线已统一（`prettier --check .` 通过）。
3. **钩子已重写为纯 POSIX sh**：`.husky/pre-commit` 只跑 lint-staged；`.husky/pre-push` 跑后端 + 前端单测，并按 `backend/venv/Scripts` → `backend/venv/bin` → `uv` 的顺序解析 Python，跨平台可用。临时跳过用 `HUSKY=0 git push`。

仍需人工把关的残余项（不在 CI 覆盖范围）：

- 覆盖率门禁只在 CI 生效（本地不加 `--cov`，避免拖慢单测）；新增核心逻辑仍需 Reviewer 看用例。
- 残留 `TODO` 未标偿还条件（见 `core/infra/postgres.py`、`circuit_breaker.py`、`main.py`），Review 时按第 8 节要求补 `TODO(phaseN): <条件>`。

---

## 2. 后端：格式与语言

### 2.1 基本格式

- 行宽 **100**（ruff 已配置），超过就换行，不要靠 `# noqa: E501`。
- 缩进 4 空格；字符串统一双引号（ruff 默认 formatter 行为），f-string 优先于 `%` / `.format()`。
- import 顺序由 ruff `I` 规则管理：标准库 → 第三方 → 本项目 `app.*`，组间空行。
- 每个模块首行写模块级 docstring，说明**这个模块负责什么、降级策略是什么**，不要写「这是一个模块」这类废话。

```python
"""工单服务（异步）。

提供：
- create_ticket：幂等创建（Redis 分布式锁 + 5 分钟内查重）
- update_status：状态机流转 + 角色权限校验

幂等策略：
- Redis 可用且锁获取成功：查重，有则返回已存在，无则创建
- Redis 不可用：降级直接创建（跳过幂等，记录日志）
"""
```

这是本仓库的既有风格（见 `core/ticket_service.py`），新模块照此写。

### 2.2 类型注解

- 公共函数（被其他模块 import 的）**必须**有参数与返回类型注解；模块内部小工具函数可省。
- 文件头统一 `from __future__ import annotations`（既有代码普遍如此，保持一致性，也让 `X | None` 在 3.11 下无痛）。
- 用 `X | None` 而不是 `Optional[X]`（ruff `UP` 会提示）。
- 不引入 mypy / pyright 门禁，所以注解靠自觉：**注解必须与实现一致**，写 `-> dict` 就别返回 `None`。

### 2.3 命名

| 对象 | 规则 | 例子 |
|---|---|---|
| 模块 | 小写下划线，单数 | `ticket_service.py`、`entity_extractor.py` |
| 类 | PascalCase | `DialogStore`、`ToolAgent` |
| 函数/变量 | snake_case，动词开头 | `create_ticket`、`_find_recent_duplicate` |
| 常量 | UPPER_SNAKE | `VALID_TRANSITIONS`、`IDEMPOTENCY_WINDOW_SECONDS` |
| 私有 | 前缀 `_` | `_ticket_to_dict`、`_lock_key` |
| 布尔 | `is_` / `has_` / `should_` 前缀 | `is_connected`、`_should_skip_retrieval` |

- 禁止 `data`、`info`、`res`、`tmp` 这类无语义变量名（循环里的 `d`、`e` 除外）。
- 领域缩写保留全大写：`LLM`、`RAG`、`SSE`、`MCP`、`BM25`、`CRAG`。

### 2.4 禁止

- `print()`（本项目为 0，保持为 0）。
- 在 `except` 中 `pass` 或只写注释（P0）。
- 裸 `except:`（必须写异常类型，最宽 `except Exception`）。
- 用 `asyncio.run()` 包裹已有 async 函数（会嵌套 event loop）。
- 在模块顶层做 I/O（连接、读文件、发请求），必须在 lifespan 或首次调用时惰性初始化。

---

## 3. 后端：分层与依赖

### 3.1 依赖方向

```
api/  →  agents/  →  core/  →  core/infra/
                 ↘  core/rag/、core/mall/、core/dialog/
```

- **单向**：下层不得 import 上层。`core/*` 里出现 `from app.api...` 一律 P0。
- `api/` 只做四件事：参数校验、权限依赖、调用 service/agent、异常 → HTTP 状态码映射。**业务逻辑不写在路由函数里**。
- `core/infra/` 只封装外部系统（PG / Redis / Qdrant / LLM / TTS / Langfuse / 断路器），不含业务语义。

### 3.2 新增文件放哪里

沿用 `AGENTS.md`「新增文件放置规则」表，不另立目录。补充约定：

- 一个业务域一个 service 文件（`ticket_service.py`、`feedback_service.py`），不要建 `services/` 大包。
- 跨域复用逻辑放 `core/` 根或对应子包，不要复制粘贴（本仓库已有 `mock_source.py` / `real_source.py` 单一数据源的先例）。
- 新增外部依赖封装必须落在 `core/infra/`，并在 `main.py` lifespan 或调用点做「未配置即 no-op / 未连接即降级」处理。

---

## 4. 后端：异步

- 全链路 async：数据库、Redis、Qdrant、LLM、TTS 一律 `await`，禁止在 async 函数里调同步阻塞库。
- 不得不调同步阻塞代码（如本地 CrossEncoder 重排、jieba 首次加载）时，用 `asyncio.to_thread(...)` 或放进 RQ 任务，不要卡住事件循环。
- 并发用 `asyncio.gather(..., return_exceptions=True)`，并对每个异常单独记日志；不要让一个分支的异常吞掉整批结果。
- **所有外部调用必须有超时**：`asyncio.wait_for(...)` 或客户端 timeout。Reranker 的硬超时护栏（`RERANKER_TIMEOUT + 5`）是既有范例，新增网络调用照此加护栏。
- 后台任务用 `asyncio.create_task(...)` 时，必须持有引用（避免被 GC）并包裹异常日志，且不能阻塞 SSE 主流程（参考摘要压缩的实现）。
- 长耗时操作（知识库重建、坏例回流、评估）**必须**走 `core/tasks` 的 `enqueue_task`，HTTP 端点秒回 `job_id`；任务函数必须模块级可序列化，禁止 lambda / 闭包。

---

## 5. 后端：配置

- 所有可调参数进 `app/config.py` 的 `Settings`，通过 `settings.XXX` 读取；**禁止**在业务代码里硬编码 URL、端口、模型名、超时、阈值。
- 新增配置项必须同步 `.env.example`（占位符，不含真实值）。
- LLM 供应商一律经 `config.py::llm_profile()` 取 `(api_key, base_url, model)` 三元组；`llm_factory` 之外**禁止**直接引用 `settings.DEEPSEEK_*` / `settings.SENSENOVA_*`（P0）。
- 新增供应商 = `config.py` 加一组 `<PREFIX>_API_KEY/_BASE_URL/_MODEL` + `.env` 三行 + `.env.example` 三行，不在 `llm_factory` 里写 if 分支。
- 开关型配置（`*_ENABLED`）默认值必须保证「不配置也能跑」：关闭时走既有路径，不是报错。

---

## 6. 后端：日志与可观测

### 6.1 日志

- 每个模块 `logger = logging.getLogger(__name__)`，禁止 `logging.info(...)` 直接用根 logger。
- 用参数化占位符，不要 f-string 拼日志（避免无谓格式化开销）：
  ```python
  logger.warning("工单会话上下文读取失败 ticket=%s conversation=%s: %s", ticket_id, conversation_id, e)
  ```
- 级别约定：

  | 级别 | 用途 |
  |---|---|
  | `debug` | 检索命中数、prompt 长度、缓存键等排障细节 |
  | `info` | 生命周期事件：服务启动、任务入队/完成、灌库条数 |
  | `warning` | **所有降级路径**（Qdrant 失败转 BM25、Redis 失败直查、LLM 切 Ollama、TTS 503） |
  | `error` | 请求最终失败且无降级路径、后台任务异常 |

- 日志必须带可定位的标识：`ticket_id` / `conversation_id` / `job_id` / `request_id` 至少一个。
- **禁止**记录：完整 prompt、用户原文全文、token、密钥、手机号、完整订单号（需要排查时打码或只打后 4 位）。

### 6.2 Langfuse 埋点

- 只在 `llm_factory.call_llm` 埋 LLM span（`name=llm.call`）；调用方只埋编排级 trace/span，**不要重复埋**。
- 埋点全程旁路：未配置 key 时 no-op，不构造客户端、不阻塞、不抛异常、不改变返回值与异常语义。
- `span.update()` / `end()` 失败只 `logger.warning`（参考 `_safe_span_update` / `_safe_span_end`）。
- 4.14 的 `start_as_current_observation()` 不支持 `async with`，async 代码用同步 `with` 包住 `await`。

---

## 7. 后端：错误处理与降级

### 7.1 异常 → HTTP 映射

业务层用**语义化异常**表达失败，路由层统一翻译，不要在 service 里抛 `HTTPException`：

| 业务异常 | HTTP | 约定 |
|---|---|---|
| `ValueError`（参数/状态非法） | 400 / 422 | 状态机非法流转 → 400；请求体字段非法 → 422 |
| `PermissionError` | 403 | 角色/归属不足 |
| 资源不存在 | 404 | 他人资源也用 404，防枚举（见 `api/ticket.py` 的工单详情） |
| 依赖不可用 | 503 | PG / 会话服务故障，文案面向用户，不暴露堆栈 |

```python
try:
    result = await update_status(...)
except ValueError as e:
    raise HTTPException(status_code=400, detail=str(e)) from e
except PermissionError as e:
    raise HTTPException(status_code=403, detail=str(e)) from e
```

- `raise ... from e` 保留因果链，不要 `raise HTTPException(...)` 丢原始异常。
- 响应体 `detail` 面向终端用户，中文、可操作；内部细节进日志，不进响应。

### 7.2 降级三要素

任何 `except` 都必须回答三个问题，缺一即 P1：

1. **兜底是什么**（返回空 / 走备用通道 / 降级 mock / 503）；
2. **怎么记录的**（`logger.warning` 带上下文）；
3. **调用方怎么知道**（返回值形状是否变化、是否需要 `degraded` 标记）。

降级路径以 `AGENTS.md`「失败降级规则」表为准，不得新增「静默返回默认值」的路径。

### 7.3 宽泛捕获的边界

`except Exception` 允许用于**旁路/降级边界**（埋点、审计、缓存、可选增强），不允许用于业务主流程的错误吞噬。使用时在 `except` 上方写一行注释说明「为什么可以吞」。

---

## 8. 后端：API 与 SSE 契约

### 8.1 路由

- 统一前缀 `/api/v1`，按域分文件（`ticket.py`、`knowledge.py`、`mall.py`…）。
- 请求体用 Pydantic `BaseModel` + `Field(..., min_length=..., max_length=..., description=...)`；不要用裸 `dict`。
- 查询参数用 `Query(..., ge=, le=)` 做边界校验，分页统一 `limit`（默认 50，上限 200）+ `offset`。
- **静态路径必须注册在动态路径之前**（`/list`、`/updates` 在 `/{ticket_id}` 之前），否则被路径参数吞掉。
- 每个端点写 docstring，说明权限规则与错误码（这是本仓库的既有风格，保持）。
- 写操作涉及权限或资金/状态流转时，必须写审计（`audit_service.record`），审计失败只 warning 不回滚业务。

### 8.2 SSE（`POST /api/v1/chat/ask`）

- 事件名是**封闭集合**：`start` / `disambiguation` / `retrieving` / `rewriting` / `generating` / `delta` / `tool_call` / `tool_result` / `done` / `error`。新增事件必须同步：后端 `_sse_event` 调用点、前端 `api/chat.js` 注释与 `Chat/index.vue` 处理分支、本文档。
- `done` 必须携带完整 `answer`（`delta` 只是增量，前端不能只依赖 delta 拼最终态）。
- `error` 事件之后不得再发 `done`；`disambiguation` 为 clarify 时序列短路为 `start → disambiguation → done`。
- 单轮只允许一次 `error` 回调（前端 `chatStream` 用 `reported` 标志保证）。
- 不要改成 WebSocket，不要改成普通 JSON（P0）。

---

## 9. 后端：数据层

- PostgreSQL + asyncpg，SQLAlchemy async；禁止 SQLite / `aiosqlite`。
- Session 生命周期由 `async_session` 上下文管理，不在函数间传递裸 session。
- 时间统一 **naive UTC** 存库（列是 `TIMESTAMP WITHOUT TIME ZONE`），写入前 `datetime.now(UTC).replace(tzinfo=None)`；对外序列化用 `isoformat()`。
- 唯一 ID 生成必须满足高并发唯一性：工单 `TK-{时间戳}{≥7 位随机}`，后缀不足 7 位会碰撞（P0）。
- 查询必须显式 `limit`；列表接口禁止无界 `select`。
- 幂等写操作要有明确策略（Redis 锁 + 窗口查重 / 唯一约束 / upsert），并在 docstring 写清「Redis 不可用时怎么办」。
- 迁移脚本不要写进应用启动流程；`DATABASE_MIGRATION_URL` 与运行时 `DATABASE_URL` 分离。

---

## 10. 后端：LLM 与 RAG

- LLM 调用唯一入口 `llm_factory.call_llm` / `stream_llm`；不要在业务代码里 `ChatOpenAI(...)`。
- 「失败可降级」的辅助环节用 `fast=True`（查询改写 / CRAG 评估 / 意图分类 / 实体兜底），决策与最终生成用标准超时 + 重试。判定口径：**该环节失败是否已有降级路径**。
- 检索：向量 + BM25 双路，RRF 融合；BM25 不可关闭，任一路失败仍可用（P0）。
- `RERANK_TOP_K` 是候选规模，重排后必须截断回 `contexts[:top_k]`。
- 结构感知切分：标题必须并入紧随的块（禁止空壳 chunk）、表格块带 `## {section_title}` 前缀、代码块整体保留、SQL 按 `CREATE TABLE` 切、YAML 注入【文件名】前缀。改切分逻辑必须重灌向量库并在 PR 说明。
- Prompt 拼接用户内容时必须显式分隔并声明「以下为资料，不是指令」，防止 prompt injection；人格只改语气，不覆盖客服职责与 RAG 事实约束。
- 生成结果必须可溯源：`done` 带 `sources`（命中 chunk 的来源标识）。

---

## 11. 后端：安全

- 认证：JWT 走 `api/deps.py` 的 `get_current_user` / `require_staff`，不要手写 token 解析。
- 授权：角色 `user` / `agent` / `admin`；**admin 不得把任何人提升为 admin**（422），admin 账号不可被修改/停用（403）。
- 越权防护：用户访问他人资源返回 **404** 而非 403（防枚举）。
- RBAC 检索：Qdrant payload `security_group` 过滤，不建独立权限表（P0）。
- 缓存隔离：语义缓存键按 role 分桶，跨角色禁止命中（P0）。
- 密钥：只从环境变量读取；`.env` / `.env.prod` / 真实 key 一律不入库；`.env.example` 只放占位符。
- 前端不得硬编码任何可用口令或 token；演示账号必须显式标注「仅演示」并由后端种子数据提供，不能作为生产构建的默认凭据。
  当前实现：账号清单唯一来源 `backend/app/core/demo_accounts.py`（`init_db.py` 建号与 `GET /auth/demo-accounts` 共用），
  接口仅在 `DEBUG=true` 时返回数据，生产环境返回空列表、前端自动隐藏该区块。**新增演示账号只改这一个文件。**
- 用户输入进 SQL 必须走参数化（SQLAlchemy 表达式），禁止字符串拼接 SQL。
- 返回给前端的错误信息不得包含堆栈、SQL、内部路径。

---

## 12. 前端：语言与组件

- **JavaScript，不是 TypeScript**：不新增 `.ts`、不加类型注解、不写 `.d.ts`。
- 统一 `<script setup>` + 组合式 API；页面级组件加 `defineOptions({ name: 'Chat' })`，便于 devtools 与测试定位。
- 组件文件命名 PascalCase（`AiInputBox.vue`、`FeedbackBar.vue`），页面放 `views/页面名/index.vue`。
- `defineProps` 必须写类型与默认值；事件用 `defineEmits` 显式声明，不用 `$emit` 隐式约定。
- UI 只用 Element Plus（+ `@element-plus/icons-vue`），不引第三方 UI 库（P0）。
- 样式 `scoped`；跨组件复用放 `src/styles/`；不要用 `!important` 压制 Element Plus，优先用 `:deep()` 精确覆盖。
- 响应式用 `src/utils/useBreakpoint.js`，不在组件里散写 `window.innerWidth` 监听（要监听必须解绑）。

### 12.1 组件规模

已完成一轮拆分（2026-10-28）：`views/Chat/index.vue` 2526 → 1827 行、`views/Admin/index.vue` 1317 → 1172 行。约定：

- 已抽出的可复用单元：`components/Chat/ChatDiagnostics.vue`（诊断面板）、`components/Admin/AdminOrders.vue`（商城订单）、
  `utils/chatDiagnostics.js`（诊断纯函数）、`utils/datetime.js`（时间格式化）、`composables/useDemoAccounts.js`（演示账号取数）。
- 新增逻辑优先抽到 `components/`、`composables/` 或 `utils/`，不要在超千行文件里继续堆。
- 单个 `<script setup>` 超过 ~400 行时，Review 必须提出拆分建议（P2，但同一 PR 内新增 200 行以上逻辑则升为 P1）。
- 抽组件时**连同 scoped 样式一起搬走**：父组件的 scoped 样式不作用于子组件内部，漏搬即视觉回归；搬完必须跑该页面的 spec + `vite build`。
- 仍在待拆清单：`Chat` 的消息渲染（工具卡/反馈条）与历史抽屉、`Admin` 的工单详情抽屉与反馈追溯。

---

## 13. 前端：状态与请求

### 13.1 Pinia

- 用 setup 语法 `defineStore('域', () => { ... })`，导出 `useXxxStore`。
- store 只存状态与派生逻辑，**不发 HTTP 请求**（请求在 `api/`）；组件调 api 后写 store。
- 需要跨刷新保留的状态显式写 `localStorage`，键名统一 `assistmind:xxx`（参考 `persona` store）。
- 不在 store 里放组件实例、DOM 引用、定时器（定时器归组件并在 `onUnmounted` 清理）。

### 13.2 请求

- 所有 HTTP 走 `src/api/*.js`，底层统一 `request.js`（axios 实例 + 拦截器）；组件里禁止裸 `axios`。
- 例外仅两类，且必须在文件头注释说明原因：SSE 流式（`api/chat.js` 用 `fetch` + `ReadableStream`）、二进制音频（`api/tts.js` 用原生 `fetch`）。
- 轮询/徽标类请求传 `{ silent: true }`，失败不弹窗、不污染 `backendStatus`，由调用方 `console.warn` 并下轮重试。
- 错误提示统一由拦截器负责（3 秒节流、同文案合并）；组件不要重复 `ElMessage.error`。
- 401 统一登出并跳登录（axios 拦截器与 SSE 路径都要处理，SSE 不能只报错）。
- 请求必须可取消：组件卸载 / 新一轮发送时 `AbortController.abort()`，并在 catch 里区分 `AbortError`。

---

## 14. 前端：交互与可观测

- 异步操作必须有 loading / disabled 态，防重复提交（尤其"发送""建单""退款"）。
- 失败必须可恢复：给出重试入口或明确下一步，不允许只弹一个 toast 后界面卡死。
- 不吞异常：`catch` 里至少 `console.warn`，且不能把 `AbortError` 当错误提示给用户。
- 禁止 `console.log` 留在提交代码里（当前为 0，保持）；排障用 `console.warn` / `console.error` 并带模块前缀。
- 大列表用分页或虚拟滚动；聊天消息列表注意滚动节流，不在 `watch` 里同步 `scrollIntoView` 造成抖动。
- 无障碍底线：可点击元素用 `button` / `el-button`，图标按钮加 `aria-label`，表单控件有 `label`。

---

## 15. 测试规范

### 15.1 后端

- 位置 `backend/tests/unit/test_功能名.py`，集成测试 `backend/tests/integration/` 并标 `@pytest.mark.integration`。
- 命名 `test_{功能}_{场景}`，docstring 写清覆盖点（本仓库既有风格，保持）。
- 单测不连真实服务：mock Qdrant / Redis / PG / LLM；Redis 队列测试用 `fakeredis` + `SimpleWorker(burst=True)`（Windows 无 `os.fork`，用 `Worker` 会崩）。
- 异步测试直接 `async def test_xxx()`，不调 `asyncio.run()`；一个用例需要多步 async 就写在一个 async 函数里。
- **契约唯一性**：同一模块只保留一份权威测试，禁止并存断言相反行为的草稿文件。
- 测「未配置」时必须显式传 None（`Settings(...=None)`），因为 `Settings` 会读本机 `.env`。
- 新增功能必须带测试；修 bug 必须带能复现该 bug 的用例（先红后绿）。

### 15.2 前端

- 位置 `frontend/tests/unit/*.spec.js`，`describe` 写中文场景，`it` 写 `test_` 前缀的英文标识（既有风格）。
- 组件测试用 `@vue/test-utils` + `jsdom`，mock `api/` 模块而不是 mock `fetch`。
- 覆盖优先级：store 持久化/降级逻辑 > 关键交互分支（发送、取消、错误态）> 纯展示。

### 15.3 覆盖率

- 门禁 70% 只在 CI 生效；本地想看：`pytest tests --cov=app --cov-report=term-missing`。
- 覆盖率不是目标，**降级分支与错误分支**的覆盖比总数字更有价值。

---

## 16. 提交规范

- Conventional Commits：`feat|fix|docs|refactor|test|chore(scope): 中文摘要`。
- 单 commit 单职责；body 用列表写「改了什么 + 为什么（可验证的技术原因）」，每条对应真实 diff。
- 文档随功能提交：改了接口/事件/配置/架构，同 commit 更新 `README.md`、`AGENTS.md` 或 `docs/`。
- `chore` 只用于 `.gitignore`、构建脚本等机械同步，不当"收尾垃圾桶"。
- 私人资料与真实密钥不入库：`.env`、个人备料、本机路径中的个人目录名、真实 key 一律排除；`.env.example` 只放占位符。
- 对外文本（commit / README / 注释）用面向公众的技术语言，不写内部动机与包装词。

---

## 17. 注释与文档

- 注释解释**为什么**，不复述代码在做什么。
- 降级、超时、并发、缓存失效这类"反直觉但有原因"的逻辑，必须写一句依据（最好带现象/数据，如"非流式 TTFT ~19s，流式 ~2s"）。
- 修 bug 引入的新状态（标志位、定时器、一次性回调），注释必须写清「谁复位、触发源不来了怎么办、迟到/重复触发怎么办」。
- 过期注释比没有注释更糟：改动逻辑时同步改注释，Review 时把"注释与实现不一致"当 P1。
- `TODO` 必须带条件：`# TODO(phase2): 连接池初始化` 可接受，裸 `# TODO` 不接受。

---

## 18. 例外与技术债

- 违反 P0 无例外；确需突破必须先改 `AGENTS.md` 的架构约束并同步本文，再改代码。
- 违反 P1 需在 PR 描述登记：`技术债: <问题> / 原因: <为什么现在不做> / 偿还条件: <何时必须做>`。
- 同一处技术债被登记两次仍未偿还，下次 Review 升为 P0。
- 新增依赖必须说明：为什么现有依赖不够、体积/许可、是否有替代。
