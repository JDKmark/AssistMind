# AssistMind — AI 编码规则

> 本文件供 Codex / Cursor / Copilot 等 AI 编码工具读取。
> 只写 AI 容易猜错、代码里读不出来的规则。

## 技术栈版本（不要猜，看这里）

- Python 3.11（不是 3.12+）
- FastAPI 0.115+ + Uvicorn（不是 Flask/Django）
- Vue 3 + Element Plus + Pinia，**JavaScript，不是 TypeScript**
- LangChain 0.3+ + LangGraph 0.2+（API 与旧版差异大）
- Qdrant 1.12+（单容器，不是 Milvus）
- Redis 7（`redis[hiredis]` 异步客户端，不是 `aioredis`）
- PostgreSQL 15（SQLAlchemy async + asyncpg）
- DeepSeek API（主）+ Ollama（备，失败降级用）
- FastMCP（MCP 协议 Python 官方 SDK）

## 常用命令（Windows PowerShell，从仓库根目录开始）

```powershell
# 后端单元测试（不需要 Docker）
cd backend; venv\Scripts\python.exe -m pytest tests -q -k "not integration"

# 后端全部测试（需要 Docker 服务）
cd backend; venv\Scripts\python.exe -m pytest tests -q

# 前端测试
cd frontend; npm run test:unit

# 启动后端开发服务器
cd backend; uvicorn app.main:app --reload --port 8001

# 启动前端开发服务器
cd frontend; npm run dev

# Docker 全部启动
docker-compose up -d
```

## 架构约束（违反会导致系统不一致）

### 后端

- **异步优先**：所有数据库 / Redis / Qdrant / LLM 调用必须用 async/await
- **aiosqlite 不适用**：本项目用 PostgreSQL + asyncpg，不用 SQLite
- **Redis 连接状态**：`connect()` 失败时必须清空 `self._pool = None`，否则 `is_connected` 误报
- **Qdrant RBAC**：权限控制通过 payload filter 按 `security_group` 字段过滤，不建独立权限表
- **意图路由配置外置**：关键词 / 语义样本在 `backend/app/data/intent_routes.json`，热加载，不硬编码进 `router/intent.py`
- **语义缓存失效**：`INCR scqa:kb:version` 版本号 O(1) 失效 + 惰性清理，不要改回 SCAN 全清
- **SSE 流式**：`/api/v1/chat/stream` 返回 SSE（6 种事件类型），不要改成 WebSocket 或普通 JSON
- **工单 ID 格式**：`TK-{时间戳}{7位随机数}`，后缀必须 >=7 位，否则高并发下 UNIQUE 约束碰撞
- **MCP 双向架构**：ToolAgent 不直接调本地工具函数，通过 MCP Client → MCP Server 调用；工具实现与 Agent 解耦
- **Retrieval Before Agency**：Agent 在已排序检索结果之上工作，不取代检索（论文 arXiv:2607.26497 启发）
- **BM25 一等公民**：BM25 不可被关闭，Qdrant 失败时 BM25 独立可用（失败降级兜底）
- **BM25 词粒度分词**：中文用 jieba 词粒度（非单字 unigram），英文/数字/下划线标识符整词保留（order_item 不拆泛词），带停用词表；改分词时保持 `_tokenize` 签名与区分度（相关文档得分显著高于无关文档，见 test_bm25_tokenize.py）
- **结构感知 chunk**：chunk_text 识别 Markdown 标题（→ section_title）、fenced 代码块整体保留（允许超限）、表格不拆行；mall.sql 按 CREATE TABLE 切块（→ table_comment）；SOURCES.md 类取材说明不入库
- **查询改写前置**：Multi-Query 默认启用（3 变体），CRAG 低分时被动改写是补充而非替代
- **Langfuse 埋点旁路**：LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 任一未配置即视为未启用（`is_langfuse_enabled()` 返回 False），埋点必须全程 no-op：不构造客户端、不阻塞、不抛异常、不改变返回值与异常语义
- **LLM 单点埋点**：所有 LLM 调用统一由 `llm_factory.call_llm` 埋点（每次调用一个 span，name=llm.call），调用方不要重复埋 LLM；调用方只负责编排级 trace/span（如 ops_diagnose）

### 前端

- **JavaScript 不是 TypeScript**：不要添加 `.ts` 文件或类型注解
- **Element Plus 优先**：UI 组件优先用 Element Plus，不引入其他 UI 库
- **Pinia 状态管理**：用 Pinia store，不用 Vuex 或 composables 替代
- **API 封装**：所有 HTTP 请求放在 `src/api/` 下，通过 `request.js` 统一封装
- **路由**：页面路由在 `src/router/index.js`，5 个页面（Login/Chat/Knowledge/Tickets/Admin）

## 失败降级规则（不可回退）

每个外部调用失败时必须有明确降级路径，**不可静默 pass**（至少 `logger.warning`）：

| 组件失败 | 降级策略 |
|---|---|
| LLM 调用失败 | 重试 1 次 → 切 Ollama → 缓存近似 → 模板兜底 |
| Qdrant 失败 | 仅 BM25 召回 |
| BM25 失败 | 仅向量召回 |
| 两路召回均失败 | 返回"未找到相关文档"+ 建议转人工 |
| Reranker 失败 | 跳过重排，用 RRF 结果 |
| Redis 缓存失败 | 跳过缓存直查 |
| Redis 记忆失败 | 用请求内上下文 |
| PostgreSQL 失败 | 工单类返回 503，聊天类不受影响 |
| Prometheus 失败 | 指标返回空 + degraded；auto 模式下整体不可用降级 mock |
| Elasticsearch 失败 | 日志/变更返回空 + degraded |
| Alertmanager 失败 | 告警返回空 + degraded |

## 项目坑点

### 路径问题

- `knowledge.py` 的 `/ingest` 调用 scripts 时路径是 `os.path.join(os.path.dirname(__file__), "..", "..", "scripts")`（两层 `..`）
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
- **OPS_DATA_SOURCE**：`auto`（默认，配置了 PROMETHEUS_URL 且健康探测通过用 real，否则降级 mock）/ `mock` / `real`
- **real 模式场景语义**：`set_active_scenario` 仅记录展示，不改变真实数据（数据即真实状态），前端有「模拟/真实数据」标签提示
- **PromQL 表达式外置**：`backend/app/data/ops_metric_exprs.json`，支持 `{service}` 占位符，文件 mtime 变化热加载，不要硬编码进代码
- **Prometheus 健康探测**：auto 模式首次访问时探测 `/-/healthy`（3s 超时，不走断路器）；探测失败整体降级 mock

### Langfuse（可观测性）

- **4.14 的 async with 坑**：`start_as_current_observation()` 返回 `_AgnosticContextManager`，**不支持 `async with`**（会抛 TypeError）；async 代码用同步 `with` 包住 `await` 即可（OTEL context 基于 contextvars，await 期间当前 span 不变；asyncio.gather 子任务会复制 contextvars，Worker 嵌套观察仍挂在父 span 下）
- `span.update()/end()` 是旁路逻辑，失败只记 `logger.warning`，绝不能抛异常影响主流程（参考 `llm_factory._safe_span_update` / `_safe_span_end`）
- 嵌套观察靠 OTEL context 自动挂到当前 span（根观察即一条新 trace），不要手动传 trace_id；trace 级 input/output 用 `set_trace_io()`（4.14 已标记 deprecated，但仍是唯一入口）

### RAGAS 评估（run_eval.py）

- **answer_relevancy 必须用 collections 新版**（`ragas.metrics.collections.AnswerRelevancy`，内部循环 strictness 次生成反向问题）：旧版 `ragas.metrics.answer_relevancy` 在 Instructor LLM（llm_factory 产物）下只调用一次、退化为单点采样（Ollama 等不支持 n>1 的端点），分数是单次余弦相似度、方差大——不要换回旧版
- **collections 版 embedding 接口是 `aembed_text/aembed_texts`**（不是旧版的 embed_query/embed_documents）；`ProjectEmbeddings` 已同时实现两套，新增 embedding 包装时注意
- **运维场景语义特性**：「诊断意图 → 枚举知识」问答下 answer_relevancy 天然偏低（0.4-0.6 区间）：反向生成的问题聚焦回答的高密度实体（参数/日志关键字）而非原问题意图。该指标不作为质量闸门；事实性看 faithfulness、检索看 context_precision/context_recall、诊断链路看 run_eval_ops.py 根因命中率

## 新增文件放置规则

| 类型 | 位置 | 命名 |
|---|---|---|
| API 路由 | `backend/app/api/` | 功能名.py |
| Agent | `backend/app/agents/` | 功能名.py |
| 核心逻辑 | `backend/app/core/` | 功能名.py 或子包 |
| 运维数据源接口 | `backend/app/core/ops/base.py` | OpsDataSource ABC（async） |
| 运维数据源实现 | `backend/app/core/ops/mock_source.py` / `real_source.py` | Mock / Prometheus+ELK |
| 运维数据源门面 | `backend/app/core/ops/data_source.py` | 配置切换 + 降级（消费方 import 此处） |
| 可观测客户端 | `backend/app/core/infra/` | prometheus.py / elasticsearch.py / alertmanager.py |
| 指标表达式映射 | `backend/app/data/` | ops_metric_exprs.json（外置，热加载） |
| 测试 | `backend/tests/` | test_功能名.py |
| 前端页面 | `frontend/src/views/页面名/` | index.vue |
| 前端 Store | `frontend/src/stores/` | 功能名.js |
| 前端 API | `frontend/src/api/` | 功能名.js |
| 知识库文档 | `knowledge/` | 按来源分子目录（ops/ 运维手册、mall/ 商城文档） |
| 知识库灌库 | `backend/scripts/` | seed_ops_kb.py / seed_mall_kb.py（结构感知切分，--reset 幂等） |
| 意图路由配置 | `backend/app/data/` | intent_routes.json |
| 评估脚本 | `backend/scripts/` | run_eval.py（RAGAS 评估）/ run_eval_ops.py（OPS 根因命中率） |
| 评估数据集 | `backend/app/data/` | eval_qa.json（OPS）/ eval_mall_qa.json（mall），question / ground_truth / adversarial 字段 |

## 测试规范

- 单元测试不需要 Docker 服务，mock 外部依赖（Qdrant/Redis/PostgreSQL/LLM）
- 集成测试标记 `@pytest.mark.integration`，需要 Docker 服务运行
- 测试函数命名：`test_{功能}_{场景}`
- 异步测试：直接用 `async def test_xxx()`
- 新增功能必须附带测试

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
