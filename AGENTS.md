# AssistMind — AI 编码规则

> 本文件供 Codex / Cursor / Copilot 等 AI 编码工具读取。
> 只写 AI 容易猜错、代码里读不出来的规则。

## 技术栈版本（不要猜，看这里）

- Python 3.11（不是 3.12+）；依赖用 uv.lock 锁定（`uv sync --frozen` 安装），目标解释器 3.11（仓库根 `.python-version`）

- FastAPI 0.115+ + Uvicorn（不是 Flask/Django）

- Vue 3 + Element Plus + Pinia，**JavaScript，不是 TypeScript**

- LangChain 0.3+ + LangGraph 0.2+（API 与旧版差异大）

- Qdrant 1.12+（单容器，不是 Milvus）

- Redis 7（`redis[hiredis]` 异步客户端，不是 `aioredis`）

- PostgreSQL 15（SQLAlchemy async + asyncpg）

- DeepSeek API（主）+ Ollama（备，失败降级用）

- FastMCP（MCP 协议 Python 官方 SDK）

- RQ 2.x（Redis 异步任务队列，任务函数必须是模块级可序列化）

- edge-tts 7+（微软 Edge 云端 TTS，免费无 key，语音播报一级音色）

## 常用命令（Windows PowerShell，从仓库根目录开始）

```powershell
# 后端单元测试（不需要 Docker）
cd backend; venv\Scripts\python.exe -m pytest tests -q -k "not integration"

# 后端全部测试（需要 Docker 服务）
cd backend; venv\Scripts\python.exe -m pytest tests -q

# 前端测试
cd frontend; npm run test:unit

# 启动后端开发服务器（端口须与 frontend/vite.config.js 代理一致：8002）
cd backend; uvicorn app.main:app --reload --port 8002

# 启动 RQ worker（消费异步任务：知识库重建/坏例回流/评估；rebuild 入队后需此进程执行）
cd backend; venv\Scripts\python.exe scripts/run_worker.py

# 启动前端开发服务器
cd frontend; npm run dev

# Docker 全部启动（基础设施；应用层 backend/frontend/worker 另加 docker-compose.app.yml）
docker-compose up -d
```

## 架构约束（违反会导致系统不一致）

### 后端

- **异步优先**：所有数据库 / Redis / Qdrant / LLM 调用必须用 async/await

- **LLM 多供应商配置**：每个供应商一组 `<前缀>_API_KEY/_BASE_URL/_MODEL` 字段（前缀 = 供应商名大写、连字符转下划线，如 `SENSENOVA_*`），`.env` 切换供应商只改 `LLM_PROVIDER` 一行；解析收口在 `config.py::llm_profile()`（专属组缺项回落 `DEEPSEEK_*` 主配置并 warning 一次），`llm_factory` 一律经 `llm_profile()` 取三元组，**禁止直接引用 `settings.DEEPSEEK_*`**；新增供应商 = config.py 加一组字段 + .env 加三行，不要在 llm_factory 里散落 if

- **aiosqlite 不适用**：本项目用 PostgreSQL + asyncpg，不用 SQLite

- **Redis 连接状态**：`connect()` 失败时必须清空 `self._pool = None`，否则 `is_connected` 误报

- **Qdrant RBAC**：权限控制通过 payload filter 按 `security_group` 字段过滤，不建独立权限表

- **意图路由配置外置**：关键词 / 语义样本在 `backend/app/data/intent_routes.json`，热加载，不硬编码进 `router/intent.py`

- **语义缓存失效**：`INCR scqa:kb:version` 版本号 O(1) 失效 + 惰性清理，不要改回 SCAN 全清

- **FAQ 语义缓存已接线**：`core/cache/semantic_cache.py` 的 L1（exact）/L2（语义相似度）缓存由 `api/chat.py` faq 意图消费——无 persona 时命中直接 done（免检索），生成成功后按 role 写入；键按 role 分桶（`assistmind:cache:l1:{role}:` / `assistmind:cache:l2:data:{role}`），跨角色禁止命中（RBAC 安全，勿合桶）；L1 用 hash 内 `_expires_at` 字段控 TTL，**禁止用 redis.set 同 key 写 string**（会 WRONGTYPE 覆盖 hash，该 bug 曾让 L1 永久 miss）；知识库重建时 seeder 调 `semantic_cache.invalidate()` 失效缓存

- **LLM 调用统一内部流式**：`llm_factory` 核心调用用 `ChatOpenAI.astream` 逐 chunk 收集（商汤网关非流式整答 TTFT \~19s、流式仅 \~2s，全链路显著提速），返回值契约不变；聊天生成通道新增 `stream_llm`（逐 token + DeepSeek→Ollama 首次产出前降级 + Langfuse span name=llm.stream），faq/chat 意图经 `engine.generate_stream` 转发 SSE `delta` 事件实现打字机；task 意图 LLM 决策保持整段（ReAct 文本协议不宜流式）

- **call\_llm 快速失败模式（fast=True）**：供「失败可降级」的辅助环节使用（查询改写 / CRAG 评估 / 意图分类 / 实体 LLM 兜底）——直接走流式快速链路（LLM\_STREAM\_TIMEOUT=10s → Ollama 6s，无重试，失败感知 \~16s），避免这些环节在商汤故障时各自等满 `LLM_TIMEOUT(30s)×重试 + LLM_FALLBACK_TIMEOUT(60s)×2 ≈ 215s`（曾让 faq 一轮感知数分钟被用户报「一直正在思考」）；Agent 决策与最终生成保持标准超时+重试（质量优先）；触发条件判断：该环节失败是否已有降级路径（rewrite→原问题、CRAG→generate、意图→unclear、实体→空 dict），有则 fast

- **失败感知预算（商汤网关间歇故障下）**：意图路由=规则/语义层快路径（毫秒-秒级，规则命中不走 LLM）；faq 一轮 = rewrite(≤16s) + 检索 + CRAG(≤16s) + 生成流式(≤16s) ≈ 最坏 \~50s 出兜底，chat 一轮 ≤16s，task 受 Agent call\_llm 标准超时约束；新增「多少钱/价格/报价」等价格类关键词在 intent\_routes.json 的 faq（规则层命中，避免价格询问误走 LLM 分类被判 unclear）

- **意图直通工具**：`ToolAgent._should_skip_retrieval` 对订单/物流/快递/退款/退货（`_BUSINESS_KEYWORDS`）与纯工单操作跳过 `search_knowledge` 前置检索——这些数据源是实时业务系统而非知识库，直接走业务工具（task 链路省去改写/embedding/召回/重排/CRAG 等待）；知识型问题仍保留 Retrieval Before Agency

- **Reranker 硬超时护栏**：`siliconflow` 路径用 `asyncio.wait_for(call_with_breaker(...), RERANKER_TIMEOUT+5)` 兜底——httpx timeout 曾偶发失效拖死整条 faq SSE（180s+）；超时返回 None 走 RRF 降级并计入断路器（连续失败后跳过重排）

- **SSE 流式**：`/api/v1/chat/ask` 返回 SSE（事件：start/**disambiguation**/retrieving/rewriting/generating/**delta**/tool\_call/tool\_result/done/error），不要改成 WebSocket 或普通 JSON；`delta` 为生成阶段逐 chunk 文本（打字机），done 仍带完整 answer；`disambiguation` 为产品消歧决策事件（phase15，仅 faq/task 意图且别名召回 ≥2 候选时发出，位于 start 之后）——resolved 载荷 {status, method, product\_id, product\_name}，clarify 载荷 {status, candidates}，clarify 时事件序列短路为 start → disambiguation → done（done.answer 为反问文案、done.disambiguation.candidates 供前端渲染候选卡片）

- **工单 ID 格式**：`TK-{时间戳}{7位随机数}`，后缀必须 >=7 位，否则高并发下 UNIQUE 约束碰撞

- **MCP 双向架构**：ToolAgent 不直接调本地工具函数，通过 MCP Client → MCP Server 调用；工具实现与 Agent 解耦

- **Retrieval Before Agency**：Agent 在已排序检索结果之上工作，不取代检索（论文 arXiv:2607.26497 启发）

- **BM25 一等公民**：BM25 不可被关闭，Qdrant 失败时 BM25 独立可用（失败降级兜底）

- **BM25 词粒度分词**：中文用 jieba 词粒度（非单字 unigram），英文/数字/下划线标识符整词保留（order\_item 不拆泛词），带停用词表；改分词时保持 `_tokenize` 签名与区分度（相关文档得分显著高于无关文档，见 test\_bm25\_tokenize.py）

- **结构感知 chunk**：chunk\_text 识别 Markdown 标题（→ section\_title）、fenced 代码块整体保留（允许超限）、表格不拆行；**标题行必须并入紧随的结构块/文本缓冲（pending 机制），禁止产生仅含标题的空壳 chunk；表格块 text 以** **`## {section_title}`** **前缀开头（否则表格脱离标题语义、浪费检索槽位）**；mall.sql 按 CREATE TABLE 切块（→ table\_comment，**表级注释会拼进检索文本前缀【注释】，embedding/reranker 才能感知语义，改动后需重灌向量库**）；\**YAML 配置（application*.yml）chunk 注入【文件名】前缀——YAML 无 Markdown 标题，裸配置块会让 LLM 无法确认归属文件而生成「资料未提及」元话语（answer\_relevancy=0）\*\*；SOURCES.md 类取材说明不入库

- **RERANK\_TOP\_K 是重排候选规模**：rerank 后必须截断回 `contexts[:top_k]`（默认 8）——候选扩大让两路排名靠后的正确文档进入重排，截断保证生成上下文不淹没噪声（engine.retrieve 已实现，勿改成直接返回全量 reranked）

- **Reranker 可切云端**：`RERANKER_PROVIDER=local`（默认，本机 CrossEncoder，CPU 一次 1-2 分钟）| `siliconflow`（SiliconFlow 免费档 `POST /v1/rerank`，同款 BAAI/bge-reranker-v2-m3，分数同为 0-1，秒级返回）；key 未配/调用失败统一返回 None 走「跳过重排用 RRF」降级，断路器与本地路径共用 "reranker"

- **BM25 元数据注入**：索引打分文本 = text + title + section\_title + table\_comment 拼接（SQL DDL 词面与自然语言查询差距大，注释词项注入后才可命中），不改变 chunk 存储文本

- **MALL\_DATA\_SOURCE 配置**：电商业务数据源 `mock`（内存演示，默认）/ `real`（PostgreSQL）/ `auto`（`SELECT 1` 健康探测通过 → real，否则降级 mock）；seed 数据必须从 `mock_source.py` 常量导入（单一数据来源，mock 与 real 永远一致），禁止在 seed 脚本里另写一份数据

- **实体识别参数补填**：订单号/商品 ID 抽取在 `app/core/mall/entity_extractor.py`（规则层确定性优先），ToolAgent `execute_tool` 按 `ENTITY_TO_TOOLS` 映射补填缺失参数；订单号正则用数字边界 `(?<!\d)20\d{9}(?!\d)`（中文语境 `\b` 不生效），不要改成 `\b` 或放宽到任意 11 位数字（会误匹配手机号）

- **查询改写前置**：Multi-Query 默认启用（3 变体），CRAG 低分时被动改写是补充而非替代

- **Langfuse 埋点旁路**：LANGFUSE\_PUBLIC\_KEY / LANGFUSE\_SECRET\_KEY 任一未配置即视为未启用（`is_langfuse_enabled()` 返回 False），埋点必须全程 no-op：不构造客户端、不阻塞、不抛异常、不改变返回值与异常语义

- **LLM 单点埋点**：所有 LLM 调用统一由 `llm_factory.call_llm` 埋点（每次调用一个 span，name=llm.call），调用方不要重复埋 LLM；调用方只负责编排级 trace/span（如 ops\_diagnose）

- **限流配置生效**：`RATE_LIMIT_PER_MINUTE` 由 `RateLimitMiddleware`（`core/infra/rate_limit.py`）消费——Redis 固定窗口按客户端 IP 计数，超限 429 + Retry-After，跳过 health/mcp；**不要删配置、不要绕过中间件**（防 LLM token 刷爆）

- **RQ 异步任务队列**：耗时操作（知识库重建 / 坏例回流 / 评估运行）必须走 `core/tasks` 的 `enqueue_task`（RQ 2.x 超时参数名是 `job_timeout`，`timeout` 会透传为函数入参）；任务函数必须是**模块级可序列化**（RQ 按模块路径引用，勿用 lambda/嵌套）；失败必 `logger` 不静默；HTTP 端点入队后秒回 job\_id，状态经 `api/jobs.py` 轮询，**前端不要阻塞等待**

- **RQ worker 冷启动**：worker 进程不经过 FastAPI lifespan（没有 `qdrant.connect()`），任务内使用外部连接前必须先按需连接（如 `rebuild_knowledge_base` 先 `await qdrant.connect()`，幂等且失败自降级），再校验 `is_connected`

- **语音 TTS 两级降级**：前端 `speech.js` 的 `speak()` 优先调后端 `POST /api/v1/tts/speak`（edge-tts 云端神经音色，免费无 key），失败/503 自动回落浏览器 `speechSynthesis`；后端合成文本 ≤500 字防刷免费接口；edge-tts 失败返回 503 + `logger.warning`，不静默；ASR 保持浏览器 Web Speech API（零后端依赖）

- **角色语气（人格库）**：语气配置外置 `app/data/personas.json`（mtime 热加载，与 intent\_routes.json 同模式），由 `core/personas.py` 提供 load/list/persona\_prompt；**人格只改语气**，拼在既有 system prompt 之后，不覆盖客服职责与 RAG 事实约束；未知 persona 回落默认 + `logger.warning`

### 前端

- **JavaScript 不是 TypeScript**：不要添加 `.ts` 文件或类型注解

- **Element Plus 优先**：UI 组件优先用 Element Plus，不引入其他 UI 库

- **Pinia 状态管理**：用 Pinia store，不用 Vuex 或 composables 替代

- **API 封装**：所有 HTTP 请求放在 `src/api/` 下，通过 `request.js` 统一封装

- **路由**：页面路由在 `src/router/index.js`，6 个页面（Login/Chat/Knowledge/Tickets/Admin/Orders）

## 失败降级规则（不可回退）

每个外部调用失败时必须有明确降级路径，**不可静默 pass**（至少 `logger.warning`）：

| 组件失败             | 降级策略                                                                                                                  |
| ---------------- | --------------------------------------------------------------------------------------------------------------------- |
| LLM 调用失败         | 重试 1 次 → 切 Ollama → 缓存近似 → 模板兜底                                                                                       |
| Qdrant 失败        | 仅 BM25 召回                                                                                                             |
| BM25 失败          | 仅向量召回                                                                                                                 |
| 两路召回均失败          | 返回"未找到相关文档"+ 建议转人工                                                                                                    |
| Reranker 失败      | 跳过重排，用 RRF 结果                                                                                                         |
| Redis 缓存失败       | 跳过缓存直查                                                                                                                |
| Redis 记忆失败       | 用请求内上下文                                                                                                               |
| 限流中间件 Redis 失败   | 放行（不误杀）+ RedisClient 记 warning；429 只对 Redis 可用时生效                                                                     |
| PostgreSQL 失败    | 工单类返回 503，聊天类不受影响；mall 数据源各方法降级（query\_order/product → None、logistics → \[]、apply\_refund → 失败 dict），auto 模式整体降级 mock |
| Prometheus 失败    | 指标返回空 + degraded；auto 模式下整体不可用降级 mock                                                                                 |
| Elasticsearch 失败 | 日志/变更返回空 + degraded                                                                                                   |
| Alertmanager 失败  | 告警返回空 + degraded                                                                                                      |
| 实体抽取 LLM 兜底失败    | 返回空实体 dict + degraded 语义，不阻塞 Agent 主链路                                                                                |
| edge-tts TTS 失败  | 后端返回 503 + warning；前端回落浏览器 speechSynthesis（两级降级，不静默）                                                                  |

## 项目坑点

### 路径问题

- `knowledge.py` 的 list/delete/rebuild 调用 Qdrant 的路径是 `os.path.join(os.path.dirname(__file__), "..", "..", "scripts")` 风格（两层 `..`）；灌库走 `scripts/seed_*_kb.py`（seeder 公共逻辑），没有 `/ingest` 端点

- 不要硬编码绝对路径，用 `os.path.dirname(__file__)` 相对计算

### 异步测试陷阱

- `pytest-asyncio` 的 `asyncio_mode = auto` 已配置，但每个 async 测试跑在独立 event loop

- 不要在 async 测试中调用 `asyncio.run()`（嵌套 event loop）

- 如果一个测试需要多个 async 操作，写成一个 `async def` 函数

### LangChain/LangGraph

- LangChain 0.3 的 `bind_tools()` 要求 tool schema 是 OpenAI function calling 格式

- LangGraph `StateGraph` 的 node 函数必须返回 dict（更新 state），不能返回 None

### 运维数据源（OPS）

- **数据源已 async 化**：`data_source.py` 门面 + mock/real 双实现，所有调用必须 `await`，禁止用 `asyncio.to_thread` 包装

- **OPS\_DATA\_SOURCE**：`auto`（默认，配置了 PROMETHEUS\_URL 且健康探测通过用 real，否则降级 mock）/ `mock` / `real`

- **real 模式场景语义**：`set_active_scenario` 仅记录展示，不改变真实数据（数据即真实状态），前端有「模拟/真实数据」标签提示

- **PromQL 表达式外置**：`backend/app/data/ops_metric_exprs.json`，支持 `{service}` 占位符，文件 mtime 变化热加载，不要硬编码进代码

- **Prometheus 健康探测**：auto 模式首次访问时探测 `/-/healthy`（3s 超时，不走断路器）；探测失败整体降级 mock

### Langfuse（可观测性）

- **4.14 的 async with 坑**：`start_as_current_observation()` 返回 `_AgnosticContextManager`，**不支持** **`async with`**（会抛 TypeError）；async 代码用同步 `with` 包住 `await` 即可（OTEL context 基于 contextvars，await 期间当前 span 不变；asyncio.gather 子任务会复制 contextvars，Worker 嵌套观察仍挂在父 span 下）

- `span.update()/end()` 是旁路逻辑，失败只记 `logger.warning`，绝不能抛异常影响主流程（参考 `llm_factory._safe_span_update` / `_safe_span_end`）

- 嵌套观察靠 OTEL context 自动挂到当前 span（根观察即一条新 trace），不要手动传 trace\_id；trace 级 input/output 用 `set_trace_io()`（4.14 已标记 deprecated，但仍是唯一入口）

### RAGAS 评估（run\_eval.py）

- **answer\_relevancy 必须用 collections 新版**（`ragas.metrics.collections.AnswerRelevancy`，内部循环 strictness 次生成反向问题）：旧版 `ragas.metrics.answer_relevancy` 在 Instructor LLM（llm\_factory 产物）下只调用一次、退化为单点采样（Ollama 等不支持 n>1 的端点），分数是单次余弦相似度、方差大——不要换回旧版

- **反向问题必须强制与回答同语言**（run\_eval.py 的 `ZhAnswerRelevancePrompt`）：默认英文指令在 DeepSeek 下随机生成英文反向问题，中文答案 + 英文问题 = 跨语言 embedding 对比无意义，ar 被随机拉低（样本级 ±0.2-0.7 波动、0.000 极端值，全量 ar 从 0.68 的假象下隐藏真实 \~0.89）；不要改回默认 prompt，不要删中文示例

- **collections 版 embedding 接口是** **`aembed_text/aembed_texts`**（不是旧版的 embed\_query/embed\_documents）；`ProjectEmbeddings` 已同时实现两套，新增 embedding 包装时注意

- **运维场景语义特性**：「诊断意图 → 枚举知识」问答下 answer\_relevancy 曾有 0.4-0.6 偏低的记录，但语言漂移修复后实测 **0.78+（常规样本 0.81）**——原结论大部分是英文反向问题造成的假象，该指标已可正常参考；事实性仍看 faithfulness、检索看 context\_precision/context\_recall、诊断链路看 run\_eval\_ops.py 根因命中率

## 新增文件放置规则

| 类型           | 位置                                                                                                                 | 命名                                                                                                    |
| ------------ | ------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| API 路由       | `backend/app/api/`                                                                                                 | 功能名.py                                                                                                |
| Agent        | `backend/app/agents/`                                                                                              | 功能名.py                                                                                                |
| 核心逻辑         | `backend/app/core/`                                                                                                | 功能名.py 或子包                                                                                            |
| 运维数据源接口      | `backend/app/core/ops/base.py`                                                                                     | OpsDataSource ABC（async）                                                                              |
| 运维数据源实现      | `backend/app/core/ops/mock_source.py` / `real_source.py`                                                           | Mock / Prometheus+ELK                                                                                 |
| 运维数据源门面      | `backend/app/core/ops/data_source.py`                                                                              | 配置切换 + 降级（消费方 import 此处）                                                                              |
| 运维诊断流水线      | `backend/app/core/ops/pipeline.py`                                                                                 | 计划/采集/分析（Agent 编排与 SSE 流式共用，agents/ops\_supervisor 只留 LangGraph 壳）                                    |
| 电商数据源实现      | `backend/app/core/mall/mock_source.py` / `real_source.py`                                                          | Mock / PostgreSQL                                                                                     |
| 电商数据源门面      | `backend/app/core/mall/data_source.py`                                                                             | 配置切换（消费方 import 此处）                                                                                   |
| 实体识别         | `backend/app/core/mall/entity_extractor.py`                                                                        | 规则抽取 + 工具参数补填映射                                                                                       |
| 电商业务模型       | `backend/app/models/mall.py`                                                                                       | MallProduct/Order/OrderItem/Logistics/Refund                                                          |
| 电商数据 seed    | `backend/scripts/seed_mall_db.py`                                                                                  | 从 mock\_source 常量导入（单一数据来源，幂等）                                                                        |
| 可观测客户端       | `backend/app/core/infra/`                                                                                          | prometheus.py / elasticsearch.py / alertmanager.py                                                    |
| 指标表达式映射      | `backend/app/data/`                                                                                                | ops\_metric\_exprs.json（外置，热加载）                                                                       |
| 测试           | `backend/tests/`                                                                                                   | test\_功能名.py                                                                                          |
| 前端页面         | `frontend/src/views/页面名/`                                                                                          | index.vue                                                                                             |
| 前端 Store     | `frontend/src/stores/`                                                                                             | 功能名.js                                                                                                |
| 前端 API       | `frontend/src/api/`                                                                                                | 功能名.js                                                                                                |
| 知识库文档        | `knowledge/`                                                                                                       | 按来源分子目录（ops/ 运维手册、mall/ 商城文档）                                                                         |
| 知识库灌库        | `backend/app/core/rag/seeder.py`（公共逻辑）+ `backend/scripts/`（seed\_ops\_kb.py / seed\_mall\_kb.py，仅保留文档加载与 metadata） | 结构感知切分，--reset 幂等；upsert 用确定性 uuid5 id（doc\_id:chunk\_index），重复 seed 不翻倍                              |
| 意图路由配置       | `backend/app/data/`                                                                                                | intent\_routes.json                                                                                   |
| RQ 异步任务      | `backend/app/core/tasks/`                                                                                          | 队列 + 任务函数（enqueue\_task / rebuild\_knowledge\_base / export\_badcases / run\_evaluation / fetch\_job） |
| 任务状态路由       | `backend/app/api/jobs.py`                                                                                          | GET /api/v1/jobs/{job\_id}（queued/started/finished/failed + result/error）                             |
| RQ worker 入口 | `backend/scripts/run_worker.py`                                                                                    | Windows 无 os.fork → SimpleWorker，Linux/容器 → fork Worker                                               |
| 语音合成客户端      | `backend/app/core/infra/tts.py`                                                                                    | edge-tts 流式合成（synthesize → AsyncIterator\[bytes]）                                                     |
| 语音播报路由       | `backend/app/api/tts.py`                                                                                           | POST /api/v1/tts/speak → audio/mpeg（首块预取，失败 503）                                                      |
| 人格库          | `backend/app/data/personas.json` + `backend/app/core/personas.py`                                                  | 外置配置（mtime 热加载）+ load/list/persona\_prompt                                                            |
| 前端语音封装       | `frontend/src/utils/speech.js` + `frontend/src/api/tts.js`                                                         | TTS 两级降级（后端 edge-tts → 浏览器合成）/ 云端合成请求（原生 fetch 二进制）                                                   |
| 评估脚本         | `backend/scripts/`                                                                                                 | run\_eval.py（RAGAS 评估）/ run\_eval\_ops.py（OPS 根因命中率）                                                  |
| 评估数据集        | `backend/app/data/`                                                                                                | eval\_qa.json（OPS）/ eval\_mall\_qa.json（mall），question / ground\_truth / adversarial 字段               |

## 测试规范

- 单元测试不需要 Docker 服务，mock 外部依赖（Qdrant/Redis/PostgreSQL/LLM）

- 集成测试标记 `@pytest.mark.integration`，需要 Docker 服务运行

- 测试函数命名：`test_{功能}_{场景}`

- 异步测试：直接用 `async def test_xxx()`

- 新增功能必须附带测试

- **RQ 任务测试**：队列用 `fakeredis`（dev 依赖已有，不连真实 Redis）+ `SimpleWorker(burst=True)`（Windows 无 `os.fork`，用 `Worker` 会崩）；任务函数单测 mock Qdrant/BM25/feedback\_service

- **TTS 测试**：客户端单测 mock `edge_tts.Communicate`（不连真实网络，断言只 yield audio 块/空白文本零调用/失败 warning+上抛）；API 用 TestClient patch `app.api.tts.synthesize`（断言 200 流式/422/503/401）

## 编辑后验证流程

```powershell
# 后端改动后
cd backend; venv\Scripts\python.exe -m pytest tests -q -k "not integration"

# 前端改动后
cd frontend; npm run test:unit
```

### 覆盖率门禁归属

- 覆盖率门禁（`--cov=app --cov-fail-under=70`）**只在 CI 生效**

- `backend/pyproject.toml` 的 `addopts` 不带 `--cov` 参数，不要加回

- 本地想看覆盖率：`pytest tests --cov=app --cov-report=term-missing`

## 提交规范（Commit Message）

- **私人资料不入库（硬规则）**：`.env`、面试 / 考试 / 简历等私人备料、个人身份信息（本机路径中的个人目录名、真实密钥等）一律不进仓库；此类文件放 `private-notes/`（已 gitignore，本地保留）。新增 / 提交任何文件（含 `.trae` 文档、代码注释、脚本示例、截图说明）前自查文件名与内容，不得携带内部意图词（面试 / 求职 / 简历 / JD / 个人目录名等）与真实密钥；`.env.example` 只允许占位符
- **以维护开源项目 / 对公众读者的话术写 commit**：像给真实开源仓库写变更说明一样——客观描述「改了什么 + 为什么改（可验证的技术原因）」，面向任何人可读，不写内部动机
- **禁止内部/包装词汇**：commit message（title/body）与 README 等对外文本中不得出现暴露内部意图的词，如「产品化」「AI 化」「包装」「为演示/面试/求职准备」等；对外就写客观事实（如「演示方式与数据口径说明」可写，「产品化改写」不可写）
- **Conventional Commits**：类型 `feat/fix/docs/chore/refactor/test` + 中文摘要；body 用列表描述具体改动
- **文档随功能提交**：功能改动涉及的 README / 架构描述随该功能 commit 一起更新，不攒到最后塞进 `chore`「收尾」；`chore` 只用于 .gitignore、构建脚本等机械同步
- **单职责 + 真实 diff**：每个 commit 只做一件事，body 每条均对应实际改动，不掺水分

## 禁止事项

- 不要把前端改成 TypeScript

- 不要引入新的 UI 框架（保持 Element Plus）

- 不要用 `asyncio.run()` 包裹已有 async 函数

- 不要创建独立的权限表（用 Qdrant payload filter）

- 不要让 LLM 直接生成 Cypher（本项目无 Neo4j，不适用）

- 不要硬编码 LLM API URL（通过 `config.py` 配置）

- 不要在 except 中静默 `pass`（至少 `logger.warning`）

- 不要把聊天接口从 SSE 改成 WebSocket

- 不要让 ToolAgent 直接调本地工具函数（必须走 MCP Client → Server）

- 不要关闭 BM25（一等公民，失败降级兜底）

## 代码探索与 CodeGraph

- 项目根 `.codegraph/` 是 Trae IDE 的代码图谱本地索引（数据库 / daemon / socket 等，`*` 已 gitignore，仅保留 `.gitignore`）；新会话可用 IDE 的 CodeGraph 面板浏览模块依赖与调用关系，无需重新扫描

- AI 侧探索代码：语义定位用 SearchCodebase，按文件/符号用 Grep/Glob，大范围架构梳理用 understand 技能生成知识图谱

- 检索代码离线可用，不依赖本机网络/服务

<!-- BEGIN: spec-项目特有规则 -->

## spec 复盘沉淀（项目特有规则）

### 测试环境污染（Settings 读 .env）

- `Settings` 配置了 `env_file=".env"`：`Settings()` 无参构造会读入本机 `.env` 的 key（如 LANGFUSE\_PUBLIC\_KEY / LANGFUSE\_SECRET\_KEY），导致「未配置」用例误判为已启用（曾致 5 个 Langfuse 测试失败）

- 测试模拟「未配置」必须显式传 None：`Settings(LANGFUSE_PUBLIC_KEY=None, LANGFUSE_SECRET_KEY=None)`；health 用例先 patch `app.core.infra.langfuse.settings` 为无 key 状态

### 测试契约唯一性

- 同一模块只保留一份权威测试（Langfuse 契约以 test\_langfuse\_infra.py 为准），禁止并存断言相反行为的草稿测试文件，否则 pytest 全绿但契约漂移

### Langfuse 构造契约

- `get_langfuse()` 只传 `(public_key, secret_key, base_url)`，不加 `additional_headers`（与已提交测试契约一致）

### 管理员安全边界

- admin 仅可在 user/agent 间调整角色，`role=admin` 一律 422（禁权限提升）；admin 账号不可修改/停用（403）

- 所有管理员写操作（角色变更 / 启停 / 退款流转）写审计；审计失败仅 logger.warning，不回滚业务变更

### 修复引入新状态的边界（标志位/定时器/回调生命周期）

- 修复本身会引入新状态，其生命周期边界是快速修复的盲区：曾出现「sendAfterStop 标志位」（onend 不触发→发送意图滞留；识别为空→吞输入文本）修完又引出「2.5s 兜底定时器 vs 迟到 onend 双重收尾」，连续两轮复审才收敛（最终以 `recognizer !== rec` 陈旧回调防御收口）

- 引入任何新状态（标志位/定时器/一次性回调）必须先答三问：**谁复位它？触发源不来了怎么办？触发源迟到或重复触发怎么办？** 答不全不动手

- 多条收尾路径必须互斥：用「会话身份」判重（陈旧回调直接 return），禁止各收尾路径各自为政

- 修复的测试必须覆盖新机制自身的失败模式（兜底先触发、事件迟到、双重触发），只测 happy path 挡不住这类回归；修完把 diff 当新代码再审一遍，专盯「这次改动引入了什么新状态/新时序」

<!-- END: spec-项目特有规则 -->
