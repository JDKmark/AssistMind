# AssistMind Code Review 检查清单

> 配套文档：`docs/code-standards.md`（规范依据）。
> 用法：按变更类型选对应小节勾选；「通用」+「对应变更类型」是必做集，其余按需。
> 最后更新：2026-10-28

---

## 0. 使用方式

| 场景 | 做法 |
|---|---|
| 日常 PR（< 400 行） | 第 1 节通用 + 第 7 节对应变更类型，15-30 分钟 |
| 大改动 / 架构改动 | 全量走一遍，重点第 4、5、7 节 |
| 热修（hotfix） | 第 1 节 + 第 9 节「修复类专项」，其余留到 24 小时内补审 |
| 自查（提交前） | 至少过第 1、8 节 |

Reviewer 的输出格式见第 10 节，**每条意见必须带影响与验证方式**，否则作者无法判断该不该改。

---

## 1. 通用检查（所有 PR 必做）

### 1.1 意图与范围

- [ ] PR 描述说清「改了什么、为什么改」，能对应到 issue / 现象 / 需求
- [ ] 没有夹带无关改动（格式化全文件、顺手重构无关模块、删除他人注释）
- [ ] 改动范围与描述一致；超预期的大 diff 已拆分或说明原因
- [ ] 涉及的文档（README / AGENTS.md / docs / 接口注释）已同步更新

### 1.2 正确性

- [ ] 主路径能走通，且**失败路径**有明确行为（不是靠"应该不会发生"）
- [ ] 边界：空值、空列表、超长输入、并发重复提交、重复点击
- [ ] 状态机 / 权限判断覆盖了所有角色（user / agent / admin）
- [ ] 时间与时区处理正确（本项目约定 naive UTC 入库）
- [ ] 没有把 `None` 当空字符串/空列表混用导致下游误判

### 1.3 可读性

- [ ] 命名能自解释，无 `data` / `tmp` / `res` 之类
- [ ] 复杂逻辑有「为什么」的注释，而不是复述代码
- [ ] 没有超过 3 层嵌套；超过就用 early return 拍平
- [ ] 没有复制粘贴产生的重复块（出现第 3 次就该抽函数）

### 1.4 测试

- [ ] 新增/修改的行为有对应用例，且**能失败**（不是恒真断言）
- [ ] 修 bug 的 PR 带复现用例（先红后绿）
- [ ] 用例覆盖降级分支与错误分支，不只 happy path
- [ ] 单测不依赖真实 Qdrant / Redis / PG / LLM / 网络
- [ ] 没有并存两份断言相反行为的测试（契约唯一性）
- [ ] 本地 `pytest tests -q -k "not integration"` 与 `npm run test:unit` 均通过

### 1.5 安全与隐私

- [ ] 无真实密钥 / token / 口令入库（含注释、示例、截图、测试夹具）
- [ ] 日志与错误响应不含 prompt 全文、手机号、完整订单号、堆栈
- [ ] 用户输入进 SQL 走参数化；进 prompt 有分隔与"非指令"声明
- [ ] 越权访问返回 404（不泄露资源存在性）
- [ ] 新增对外端点有认证依赖与角色校验
- [ ] 前端未硬编码任何可用凭据

### 1.6 可观测性

- [ ] 新增外部调用失败时有 `logger.warning`（不是静默返回默认值）
- [ ] 日志带可定位标识（`ticket_id` / `conversation_id` / `job_id` / `request_id`）
- [ ] 新增耗时环节没有破坏既有埋点契约（LLM 埋点只在 `llm_factory`）

---

## 2. 后端：异步与并发

- [ ] 数据库 / Redis / Qdrant / LLM 调用全部 `await`，无同步阻塞调用混入
- [ ] 同步重活（本地重排、jieba、文件 IO）走 `asyncio.to_thread` 或 RQ
- [ ] 每个外部调用有超时（`wait_for` 或客户端 timeout），无裸 `await` 无限等待
- [ ] `asyncio.gather` 用了 `return_exceptions=True` 且逐项处理异常
- [ ] `create_task` 持有引用、包了异常日志、不阻塞 SSE 主流程
- [ ] 没有 `asyncio.run()` 嵌套；没有在 async 里调 `time.sleep`
- [ ] 连接获取失败时状态被正确复位（如 Redis `connect()` 失败清 `_pool`）
- [ ] 长耗时操作走 `enqueue_task`，HTTP 秒回 `job_id`；任务函数模块级可序列化
- [ ] RQ 任务内使用外部连接前先 `await xxx.connect()`（worker 不走 lifespan）

---

## 3. 后端：错误处理与降级

- [ ] 每个 `except` 都有：兜底行为 + `logger.warning` + 调用方可感知的返回
- [ ] 没有 `except: pass`、裸 `except`、吞掉异常只写注释
- [ ] 宽泛 `except Exception` 仅出现在旁路边界（埋点/审计/缓存），且有注释说明
- [ ] service 层不抛 `HTTPException`；路由层做异常 → 状态码映射
- [ ] `raise ... from e` 保留因果链
- [ ] 降级路径与 `AGENTS.md` 的降级表一致（LLM→Ollama、Qdrant→BM25、Reranker→RRF、Redis→直查…）
- [ ] 降级时返回值形状未破坏调用方契约（必要时带 `degraded` 标记）
- [ ] 新增的失败模式已补进降级表（文档与代码同步）

---

## 4. 后端：API / SSE / 数据层

### 4.1 路由

- [ ] 请求体是 Pydantic 模型，字段有 `min_length` / `max_length` / `description`
- [ ] 分页参数有 `ge` / `le` 边界；列表查询有 `limit`
- [ ] 静态路径注册在 `/{id}` 之前
- [ ] 状态码用 `status.HTTP_*` 常量，不写魔法数字
- [ ] 权限规则写在 docstring；写操作有审计（失败只 warning，不回滚业务）
- [ ] 路由函数里没有业务逻辑（只做校验/依赖/调用/映射）
- [ ] admin 相关：不能提权到 admin（422）、admin 账号不可改/停（403）

### 4.2 SSE

- [ ] 事件名在封闭集合内（`start/disambiguation/retrieving/rewriting/generating/delta/tool_call/tool_result/done/error`）
- [ ] 新增事件已同步前端解析与文档
- [ ] `done` 带完整 `answer`（及 `sources`）
- [ ] `error` 之后不再发 `done`；clarify 场景短路为 `start → disambiguation → done`
- [ ] 生成阶段仍然逐 chunk 发 `delta`（没有退回整段一次性返回）
- [ ] 没有把接口改成 WebSocket / 普通 JSON

### 4.3 数据层

- [ ] 写入时间用 naive UTC；序列化用 `isoformat()`
- [ ] 唯一 ID 后缀 ≥ 7 位随机（工单 `TK-`）
- [ ] 幂等策略明确，且写了"Redis 不可用时怎么办"
- [ ] 事务边界正确：没有在循环里逐条 commit 造成半成品
- [ ] 迁移/建表改动与模型定义一致，且不混进应用启动流程

---

## 5. 后端：配置、LLM 与 RAG

### 5.1 配置

- [ ] 新增可调参数进 `Settings`，未硬编码 URL / 端口 / 模型名 / 阈值
- [ ] `.env.example` 同步（只放占位符）
- [ ] 开关默认值保证"不配置也能跑"
- [ ] LLM 三元组经 `llm_profile()` 获取，未直读 `settings.DEEPSEEK_*`

### 5.2 LLM

- [ ] 调用走 `call_llm` / `stream_llm`，未自建客户端
- [ ] 辅助环节用 `fast=True`（改写/CRAG/意图/实体兜底），决策与生成用标准超时
- [ ] 没有重复埋 LLM span
- [ ] 生成通道保持流式（非流式会让 TTFT 从 ~2s 退化到 ~19s）
- [ ] 人格只拼语气，未覆盖客服职责与 RAG 事实约束

### 5.3 RAG

- [ ] 向量 + BM25 双路仍在，BM25 未被关闭；单路失败可降级
- [ ] 重排后截断回 `contexts[:top_k]`
- [ ] 切分改动保持：标题并入后续块（无空壳 chunk）、表格带 `## 标题` 前缀、代码块完整、SQL 按 `CREATE TABLE`、YAML 带【文件名】
- [ ] 切分/embedding 改动后**已重灌向量库**并在 PR 说明
- [ ] 缓存失效走 `INCR kb:version`，未改回 SCAN 全清
- [ ] 语义缓存键按 role 分桶，跨角色不命中
- [ ] L1 缓存仍用 hash 内 `_expires_at` 控 TTL（未用 `redis.set` 覆盖同 key）

---

## 6. 前端

### 6.1 语言与结构

- [ ] 纯 JavaScript，未引入 `.ts` 或类型注解
- [ ] `<script setup>` + `defineOptions({ name })`；props/emits 显式声明
- [ ] UI 只用 Element Plus，未引入其他 UI 库
- [ ] 页面在 `views/页面名/index.vue`，复用组件在 `components/`，工具在 `utils/`
- [ ] 超千行文件（Chat 1827 / Admin 1172）未继续堆逻辑，新逻辑已外抽到 `components/` / `composables/` / `utils/`
- [ ] 抽组件时 scoped 样式随模板一起搬走（父级 scoped 不作用于子组件内部）
- [ ] 改了前端文件后 `npm run lint` 与 `prettier --check .` 均通过（CI 阻断项）

### 6.2 状态与请求

- [ ] HTTP 走 `src/api/*.js` + `request.js`，组件内无裸 axios
- [ ] 例外（SSE / 二进制音频）在文件头注释了原因
- [ ] store 不发请求、不放 DOM/定时器；持久化键名 `assistmind:xxx`
- [ ] 轮询类请求用 `{ silent: true }`，失败不弹窗不污染全局状态
- [ ] 组件未重复 `ElMessage.error`（交给拦截器）
- [ ] 请求可取消，`AbortError` 不当错误提示

### 6.3 交互

- [ ] 异步操作有 loading / disabled，防重复提交
- [ ] 失败可恢复（有重试或明确下一步），不是 toast 完就卡死
- [ ] `catch` 不吞异常，至少 `console.warn`
- [ ] 提交代码无 `console.log`
- [ ] 图标按钮有 `aria-label`；表单控件有 label
- [ ] 定时器 / 事件监听在 `onUnmounted` 清理

---

## 7. 变更类型专项

### 7.1 新增 API 端点

- [ ] 有认证依赖与角色校验
- [ ] 请求/响应模型明确，错误码写进 docstring
- [ ] 路由顺序正确（静态在动态前）
- [ ] 有对应单测（成功 / 参数非法 / 无权限 / 依赖故障）
- [ ] 前端 `api/` 有对应封装，错误提示走拦截器

### 7.2 新增 MCP 工具

- [ ] 工具实现与 Agent 解耦（ToolAgent 经 MCP Client 调用，不直接调本地函数）
- [ ] 参数补填映射（`ENTITY_TO_TOOLS`）已按需登记
- [ ] 工具失败返回结构化错误，不抛异常打断 ReAct 循环
- [ ] 有单测覆盖正常返回与失败返回

### 7.3 修改 prompt / 意图路由

- [ ] 关键词/语义样本改在 `intent_routes.json`，未硬编码进 `router/intent.py`
- [ ] 规则层命中路径仍不走 LLM（保持毫秒级）
- [ ] 新增问法有回归用例（含指代追问）
- [ ] prompt 改动有对照说明（改前/改后行为差异），不是"感觉更好"

### 7.4 修改切分 / embedding / 重排

- [ ] 已重灌向量库，或明确说明"需重灌"并给出命令
- [ ] 有区分度测试（相关文档得分显著高于无关）
- [ ] 检索质量变化有数据（命中率 / RAGAS 指标），不是主观判断
- [ ] 未破坏 `RERANK_TOP_K` → `top_k` 截断

### 7.5 缓存相关改动

- [ ] 失效策略是 O(1) 版本号，不是全量 SCAN
- [ ] 键包含 role 分桶（RBAC）
- [ ] TTL 控制方式与既有实现一致（L1 hash 内字段，不是覆盖写 string）
- [ ] 缓存命中/未命中都有日志或埋点

### 7.6 新增配置项 / 供应商

- [ ] `config.py` + `.env` + `.env.example` 三处同步
- [ ] 经 `llm_profile()` 解析，未在 factory 里堆 if
- [ ] 缺省值安全（不配置不崩）
- [ ] 文档（README / AGENTS.md）已说明如何切换

### 7.7 依赖升级

- [ ] 说明升级原因与影响面（LangChain / LangGraph / RQ 的 API 变动尤其注意）
- [ ] `uv.lock` / `package-lock` 变更与 `pyproject` / `package.json` 一致
- [ ] 全量单测通过；涉及 SSE / 检索 / 任务的路径手动验证
- [ ] 没有顺手升级无关依赖

### 7.8 修 bug（重点）

修复本身会引入新状态，这是最容易被放过的地方。必须答全三问：

- [ ] **谁复位它？**（标志位 / 定时器 / 一次性回调的清理点）
- [ ] **触发源不来了怎么办？**（兜底路径是否存在）
- [ ] **触发源迟到或重复触发怎么办？**（陈旧回调 / 双重收尾是否有身份判重）
- [ ] 多条收尾路径互斥，用"会话身份"判重而非各自为政
- [ ] 测试覆盖新机制自身的失败模式（兜底先触发、事件迟到、双重触发），不只 happy path
- [ ] 把 diff 当新代码再审一遍，专盯"这次改动引入了什么新状态/新时序"

---

## 8. 本仓库高频陷阱速查

Review 时优先扫这些，命中率高：

| # | 陷阱 | 位置 | 检查点 |
|---|---|---|---|
| 1 | 语义缓存 L1 被 `redis.set` 覆盖成 string → 永久 miss | `core/cache/semantic_cache.py` | 仍用 hash + `_expires_at` |
| 2 | 缓存键未按 role 分桶 → 跨角色泄露 | 同上 | 键含 `{role}` |
| 3 | 直读 `settings.DEEPSEEK_*` 绕过供应商抽象 | `core/infra/llm_factory.py` 之外 | 只用 `llm_profile()` |
| 4 | 重排后未截断，噪声淹没上下文 | `core/rag/engine.py` | `contexts[:top_k]` |
| 5 | 切分产生只有标题的空壳 chunk | `core/rag/chunking.py` | pending 机制仍在 |
| 6 | 表格块丢失 `## 标题` 前缀 → 语义脱离 | 同上 | 前缀存在 |
| 7 | 辅助环节用标准超时 → 一轮等 200s+ | 改写 / CRAG / 意图 / 实体 | `fast=True` |
| 8 | 意图关键词硬编码进代码而非 JSON | `core/router/intent.py` | 配置外置 |
| 9 | 工单号后缀 < 7 位 → 高并发 UNIQUE 碰撞 | `models/ticket.py` | 随机位 ≥ 7 |
| 10 | 订单号正则用 `\b` 或放宽到任意 11 位 → 误匹配手机号 | `mall/entity_extractor.py` | `(?<!\d)20\d{9}(?!\d)` |
| 11 | RQ 超时参数写成 `timeout`（被透传为函数入参） | `core/tasks/` | 用 `job_timeout` |
| 12 | worker 里没先 `connect()` 就用外部连接 | `core/tasks/__init__.py` | 幂等 connect + `is_connected` |
| 13 | 静态路由被 `/{id}` 吞掉 | `api/ticket.py` | `/list`、`/updates` 在前 |
| 14 | 越权返回 403 泄露资源存在性 | 资源详情类端点 | 统一 404 |
| 15 | 测试里 `Settings()` 读到本机 `.env` 导致误判 | 配置类测试 | 显式传 `None` |
| 16 | 两套断言相反的测试并存 → 契约漂移 | `tests/unit/` | 只留一份权威 |
| 17 | Langfuse 埋点抛异常影响主流程 | `core/infra/langfuse.py` | 只 warning，不抛 |
| 18 | 前端在超千行组件里继续堆逻辑 | `views/Chat`、`views/Admin` | 抽组件 |

---

## 9. 反模式：Reviewer 自查

Review 质量本身也要 Review。出现以下情况说明这次 Review 没做到位：

- [ ] 只提了格式/命名，没看降级路径和失败分支
- [ ] 看到大 diff 就 LGTM（"看着没问题"）
- [ ] 意见没有说明影响，作者只能猜
- [ ] 把"我觉得可以更好"当成阻塞项，把真 P0 放过去
- [ ] 没验证作者说的"测试已过"，也没让作者贴输出
- [ ] 没检查文档是否随功能更新
- [ ] 对修复类 PR 只看"bug 是否修好"，没看"引入了什么新状态"

---

## 10. 意见格式与合并门禁

### 10.1 单条意见模板

```
[P1] <文件>:<行号> — <问题一句话>

影响：<会导致什么，谁受影响，什么条件下触发>
建议：<具体改法，能贴代码就贴>
验证：<怎么确认改对了：用例 / 命令 / 手动步骤>
```

示例：

```
[P0] backend/app/core/cache/semantic_cache.py:145 — L1 用 redis.set 写同 key

影响：会把 hash 覆盖成 string，后续 hgetall 抛 WRONGTYPE，L1 永久 miss（历史已踩过）
建议：保持 hset + 内部 _expires_at 控 TTL，不新增 set 调用
验证：跑 test_semantic_cache.py，断言二次命中走 L1；手动 hgetall 确认类型仍为 hash
```

### 10.2 级别与门禁

| 级别 | 含义 | 合并 |
|---|---|---|
| **P0** | 架构红线 / 安全 / 数据正确性 / 静默失败 | 禁止合并 |
| **P1** | 缺降级日志、缺测试、硬编码、契约不一致 | 修完或登记技术债后可合并 |
| **P2** | 可读性、拆分建议、命名 | 可合并，建议后续处理 |
| **P3** | nit（空格、措辞） | 可忽略 |

- 一个 PR 内 P0 数量 > 0 时，Reviewer 必须明确写出"阻塞原因"，不能只留 emoji。
- 作者对 P1 有异议时，结论写进 PR 描述（"不改 + 理由 + 偿还条件"），不留悬空讨论。
- 同一处技术债第二次被登记 → 升级为 P0。

### 10.3 合并前最后 10 问

1. 这个改动失败时，用户看到什么？会不会卡住？
2. 有没有一条路径是静默失败（无日志、无提示、返回默认值）？
3. 并发/重复触发会怎样？
4. 权限最弱的角色（user）能不能碰到不该碰的数据？
5. 回滚容易吗？需要数据迁移吗？
6. 新配置不填会不会崩？
7. 测试真的能挡住这个 bug 复发吗？
8. 文档/注释和代码一致吗？
9. 有没有夹带无关改动？
10. 如果三个月后别人来改这块，注释够不够他看懂"为什么"？

---

## 11. 待收敛项（2026-10-28 复核）

上一轮梳理出的 5 项已修复 4 项，本节保留为「当前状态 + 剩余事项」，避免清单与仓库脱节。

**已收敛（Reviewer 可依赖自动化拦截）**

| 项 | 处置 |
|---|---|
| CI lint 为 `--exit-zero`，不阻断 | 已去掉 `--exit-zero`；存量 117 条告警（E501 48 / SIM117 21 / B905 12 / I001 10…）已清零 |
| 前端无 ESLint / Prettier，`lint-staged` 空转 | 新增根 `package.json` + `eslint.config.js` + `.prettierrc.json`；CI 增加 `eslint .`、`prettier --check .` 两步（均阻断） |
| `pre-commit` 混写 sh 与 PowerShell | 重写为纯 POSIX sh。该钩子此前**从未真正执行**（无根 `package.json`、无 `.husky/_`、`core.hooksPath` 未设置），现由 husky 安装生效：pre-commit 跑 lint-staged，pre-push 跑两端单测 |
| 登录页硬编码演示账号口令 | 账号清单移入 `backend/app/core/demo_accounts.py`（建号与接口共用），`GET /auth/demo-accounts` 仅 `DEBUG=true` 下发；前端改为运行时拉取，失败或为空即隐藏 |

**仍待处理**

| 优先级 | 项 | 位置 | 建议 |
|---|---|---|---|
| P2 | 超千行页面仍有两处 | `views/Chat/index.vue`（1827 行）、`views/Admin/index.vue`（1172 行） | 继续按职责外抽：Chat 的消息渲染（工具卡/反馈条）与历史抽屉；Admin 的工单详情抽屉与反馈追溯。抽组件必须连同 scoped 样式一起搬，并跑该页 spec + `vite build` |
| P3 | 残留 `TODO` 无偿还条件 | `core/infra/postgres.py`、`circuit_breaker.py`、`main.py` | 补 `TODO(phaseN): <条件>` |

> 拆分 `Admin` 订单面板时，`Admin.spec.js` 有 4 条用例直接读 `wrapper.vm.orders/orderPage/...`。
> 抽组件后这些断言改为 `wrapper.findComponent(AdminOrders).vm.*`，**断言的行为不变**；后续再拆其他区块按同一模式处理。

