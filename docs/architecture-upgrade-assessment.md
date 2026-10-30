# 架构升级评估：AssistMind 是否该对标「企业级多 Agent 中台」方案

> 评估对象：外界一份《高性能弹性扩展智能体中台架构：FastAPI+Nacos+RocketMQ+LangGraph+Milvus+ES+Redis+MySQL》方案。
> 评估视角：**在不重复建设的前提下**，哪些值得吸收、哪些是负资产、哪些应等条件成熟再做。
> 评估依据：本仓库当前代码与文档（不是猜测），关键出处随文标注。
> 结论日期：2026-10-18 · 适用版本：`main` @ `5a8d3e8`

---

## 一、结论（先看这一页）

**不建议整体对标。** 那份方案的隐含前提是「多业务线接入 + 多租户 + 多实例 + 有人值守的中间件运维」，而 AssistMind 当前是「单业务电商客服 + 单机 Docker Compose（目标 2C4G）+ 已有完整 RAG/Agent 链路」。整包引入会造成三类损失：

1. **重复建设**：Redis / 异步队列 / 向量库 / 全文检索 / LLM 可观测 / 应用指标 这 6 项本项目已有实现，方案里对应的是「同能力换一个更重的产品」，属于横向替换而非能力增量。
2. **资源不可行**：方案新增的 Nacos + RocketMQ + Milvus + ES + MySQL 按最小可用部署估算需 **6–10GB 内存**，叠加现有 qdrant/redis/pg/langfuse 栈后，2C4G 主机直接不可行（见 §四资源账）。
3. **打不到真实瓶颈**：本项目自己的实测基线（`docs/perf-baseline-2026-09-11.md`）已定位瓶颈在**单进程 CPU（本机 embedding/重排推理）与生成延迟**，而非「缺少服务发现 / 消息中间件 / 分布式向量库」。换中间件不改善这条曲线。

**真正值得做的，是方案里 4 个「概念正确但不必买对应产品」的点，加上本项目已规划未落地的 4 项加固**，合计 6–8 项、约 10–14 人日，全部可在现有技术栈内完成：

| 优先级 | 事项 | 来源 | 成本 |
|---|---|---|---|
| P0 | 链路过载保护：在途并发上限（快速 503）、连接池/HTTP 客户端复用、Worker 并发上限 | 本项目 spec T1/T2/T7 + 方案「弹性扩缩容」 | 3–5 人日 |
| P0 | 缓存击穿保护（热点问题并发首访合并） | 本项目 spec T4 | 2 人日 |
| P1 | 运行时可调参数与提示词外置（**不引 Nacos**，扩展既有 mtime 热加载模式） | 方案「配置中心热更新」 | 2 人日 |
| P1 | 可观测闭环：Prometheus 抓取 + Grafana 面板 + 告警规则（`/metrics` 已存在） | 方案「Prometheus+Grafana+告警」 | 1–2 人日 |
| P1 | 异步任务治理：失败任务可查/可重试、积压指标、入库幂等键（**不换 RocketMQ**） | 方案「死信队列/重试/幂等」 | 2 人日 |
| P2 | 会话状态服务端收口（不再由前端传全量 history，支持会话恢复/多实例） | 方案「LangGraph 状态持久化」 | 2–3 人日 |
| P2 | CPU 密集侧剥离：embedding / reranker 独立进程或服务 | 方案「Embedding 独立部署」+ 实测瓶颈 | 3–4 人日 |

**明确不做**：Nacos、RocketMQ/Kafka、Milvus、Elasticsearch、MySQL、K8s/HPA、通用多 Agent 编排、多租户 Collection 隔离。理由逐条见 §三、§四。

---

## 二、现状盘点（评估的前提事实）

| 能力 | AssistMind 现状 | 证据 |
|---|---|---|
| Web 服务 | FastAPI 0.141 + 全异步 + SSE 流式 | `backend/app/main.py`、`backend/app/api/chat.py` |
| 服务发现 / 配置中心 | **无独立组件**。配置走 `.env` + Pydantic Settings；`app/data/*.json` 两处 mtime 热加载；故障注入覆盖文件 mtime 热加载 | `backend/app/config.py`、`core/router/intent.py`、`core/personas.py`、`core/infra/fault_injection.py` |
| 异步任务 | RQ 2.11 复用 Redis（队列 `assistmind`），3 类任务，`Retry(max=2)` + `job_timeout` + 归属校验，独立 worker 容器，前端轮询状态 | `core/tasks/__init__.py`、`api/jobs.py`、`scripts/run_worker.py`、`docker-compose.app.yml` |
| Agent 编排 | LangGraph 1.2.10 单 Agent ReAct StateGraph（think↔act + Loop Breaker），**无 checkpointer、无多 Agent** | `agents/base.py`、`agents/tool_agent.py` |
| 向量检索 | Qdrant 单 collection，payload 过滤做 RBAC（`security_group`）与文档启停（`enabled`） | `core/infra/qdrant.py` |
| 全文检索 | 进程内 BM25（jieba 词粒度），Redis 版本号跨进程失效与惰性重建 | `core/rag/bm25.py` |
| 缓存 | Redis 7：L1 精确（hash + `_expires_at`）+ L2 语义相似度，按 role/persona 分桶，版本号 O(1) 失效 | `core/cache/semantic_cache.py` |
| 关系库 | PostgreSQL 15 + SQLAlchemy async + asyncpg（连接池为框架默认值） | `core/infra/postgres.py` |
| 可观测 | 应用 `/metrics`（10 个 `assistmind_*`，独立 registry、全程 no-op 兜底）+ Langfuse LLM/会话 trace + `[Perf]` 结构化日志 | `core/infra/metrics.py`、`main.py`、`api/chat.py` |
| 可靠性 | 断路器（aiobreaker）+ 全链路降级表 + 故障注入开关 + Redis 固定窗口限流（fail-open 为设计决策） | `core/infra/circuit_breaker.py`、`fault_injection.py`、`rate_limit.py` |
| 压测与基线 | Locust S1–S7 + 4 套 profile + 分析/门禁脚本；已出拐点与故障注入四轮取证 | `backend/tests/load/`、`docs/perf-baseline-2026-09-11.md` |
| 部署 | 单机 Docker Compose（基础设施 + backend/frontend/worker），目标 2C4G 轻量云 | `docker-compose*.yml`、`deploy/README.md` |
| 多租户 | **无**（只有 user/agent/admin 角色 RBAC，没有 tenant/org 维度） | `core/security/`、`api/deps.py` |
| 会话状态 | 前端按轮传 `history`；服务端落 PG 只用于「历史会话」回看，不参与推理 | `schemas/chat.py`、`api/conversation.py`、`core/conversation_service.py` |

**知识库规模**：`knowledge/` 共 23 个文件、约 147KB，单 collection，chunk 量级为千级。这是判断「要不要 Milvus / ES」的关键量纲事实。

---

## 三、逐项对标

符号：❌ 不建议 · 🔁 已有（横向替换无增量）· ✅ 建议吸收（不必引对应产品）· ⏳ 条件触发

| 方案组件 | AssistMind 现状 | 判断 | 理由与替代 |
|---|---|---|---|
| FastAPI 在线/Worker 双服务 | 已有，且 backend 与 worker 是两个独立容器，可独立扩缩 | 🔁 | 无增量 |
| Nacos 服务注册发现 | 单机 3 个应用容器，无跨机调用 | ❌ | 服务发现解决的是「实例地址动态变化」，单机 compose 用容器名即可；引入后是纯运维负债 |
| Nacos 动态配置 | 仅 JSON 两处 + 故障注入热加载，RAG 参数/提示词仍需重启 | ✅ | **只吸收能力不吸收组件**：把 mtime 热加载模式扩展到 `RAG_RUNTIME_PARAMS` 与提示词模板，配 admin 读写端点 + 校验（见 §五 S3） |
| Nacos 权重灰度 | 无实例组 | ❌ | 单实例部署没有灰度对象；真要灰度先要有多实例与流量入口 |
| RocketMQ 异步解耦 | RQ 已覆盖文档入库/重建/评估三类异步任务 | 🔁 | 任务量与吞吐距 RQ 上限极远；换 MQ 只增加运维面 |
| RocketMQ 死信 / 重试 / 幂等 / 顺序 | `Retry(max=2)` 有；失败任务无查询/重试入口；幂等靠「先 embed 后删旧」+ 唯一约束 | ✅ | 吸收语义：失败任务列表/重试端点、任务积压指标、入库幂等键（见 S5）。顺序性当前不构成问题（单文档内串行 + 全量重建） |
| LangGraph 编排 | 已用（单 Agent ReAct 图） | 🔁 | 已是最新版本（1.2.10） |
| LangGraph Checkpointer / 断点续跑 | 无 checkpointer | ✅（降级实现） | 「服务端会话状态」有价值，但**不必引 `langgraph-checkpoint-redis`**：会话状态落 PG（权威）+ Redis（热态）即可，且能顺带修「前端传全量 history」的正确性隐患（见 S6） |
| 多 Agent 协作（规划/检索/校验/总结） | 单 Agent + 工具链；另有查询改写、CRAG 评审、产品消歧等**管道式**环节 | ❌ | 客服场景的决策深度不需要多 Agent 协商；管道 + 单 Agent 已覆盖同样的分解，且延迟更低。强行多 Agent 会推高首 token 延迟（本项目实测 TTFT 已是用户感知第一约束） |
| Milvus | Qdrant 单 collection，千级 chunk | ❌ | 量纲差 3–4 个数量级；Qdrant 的 payload 过滤能力足以承担 RBAC + 启停过滤。项目约定亦明确「Qdrant 单容器，不是 Milvus」（`AGENTS.md`） |
| Elasticsearch 全量检索 | BM25(jieba) + 实体正则（订单号）+ Qdrant payload 过滤 | ❌ | 方案要解决的「专有名词/编号精准匹配」本项目已由 BM25 + 实体规则覆盖；且 147KB 知识库上 ES 的收益为零、成本为 1.5–2GB 内存 |
| Redis | 缓存 / 限流 / 幂等（`SET NX EX`）/ 版本号广播 / RQ 队列，用法已超方案列举 | 🔁 | 已是「一库多用」 |
| MySQL | PostgreSQL 15 + asyncpg | ❌ | 反向替换。PG 生态更强，且未来若向量规模仍小可用 pgvector 做**合并**（少一个中间件），与方案方向相反 |
| K8s / HPA / Operator | 单机 compose | ⏳ | 触发条件：多机部署或 SLA 要求 > 99.5%。当前单机 HPA 无意义 |
| OpenTelemetry 全链路 | Langfuse 已覆盖 LLM/会话级 trace；无跨组件 OTel | ⏳ | LLM 侧已解决（这是最贵的一侧）。跨 Qdrant/PG 的 OTel 需配套 collector + 后端（Jaeger/Tempo），成本中高、当前收益有限 |
| Prometheus + Grafana + 告警 | `/metrics` 端点已有，但**未部署抓取与面板**，无告警 | ✅ | 最低成本的高价值项：compose 加两个小容器 + scrape 配置 + dashboard JSON + 4 条告警规则（见 S4） |
| Loki / ELK 日志 | 无集中日志 | ⏳ | 单机 `docker logs` 够用；先把指标告警做起来 |
| Embedding 独立部署 | 与在线服务同进程（`sentence-transformers` 本机推理） | ✅ | 与实测瓶颈完全对齐：50 并发时进程 CPU 峰值 343%，吞吐停在 ~4.5 QPS。剥离后在线服务不再被 embedding/重排抢核（见 S7） |
| OpenAI 兼容接口 | 自有 SSE 协议 | ⏳ 可选 | 若确有第三方按 OpenAI 协议接入的需求再做；否则属于叙事性功能 |

---

## 四、为什么不整包引入（成本账）

### 4.1 资源账（估算，非实测）

| 组件 | 最小可用内存（常见部署经验值） | 说明 |
|---|---|---|
| Nacos 单机 | 0.5–1GB | JVM + 配置/注册数据 |
| RocketMQ（nameserver + broker） | 1–2GB | 双 JVM 进程 |
| Milvus standalone | 2–4GB | etcd/minio 依赖 + 索引驻留 |
| Elasticsearch 单节点 | 1.5–2GB | JVM heap 1G 起 + 堆外 |
| MySQL | 0.3–0.8GB | — |
| **新增合计** | **≈ 6–10GB** | 尚未计入额外磁盘与镜像体积 |

现有栈（qdrant + redis + postgres + langfuse[postgres/clickhouse/minio/redis/worker] + backend + frontend + worker）在 2C4G 上已经很紧（`deploy/README.md` 选择 4G 正因 torch 常驻）。整包引入等价于把部署门槛抬到 8C16G 以上。

### 4.2 隐性成本

- **故障面**：每多一个中间件，就多一种「服务起不来/连不上/版本不兼容」的启动失败模式，且都需要写降级分支——与项目「每个外部调用必须有明确降级路径」的约束叠加，工作量是非线性的。
- **启动与 CI**：镜像体积（torch 已 2–3GB）、compose 拉起时间、CI 依赖安装时间都会显著变差。
- **离线可跑性**：项目当前设计支持「mock 数据源 + 本机 Ollama」离线演示，重中间件会破坏这一点。
- **判断力成本**：堆组件本身不能证明架构能力；**说清「为什么不用」比「用了几个」更难也更有价值**，而本项目已有实测拐点、四轮故障注入取证、RAGAS 双闸门——这些才是可验证的工程证据。

---

## 五、建议吸收的 7 项（含落点与验收）

> 约定：全部在现有技术栈内完成，不新增中间件（Prometheus/Grafana 为可选 profile）。新增参数一律进 `config.py` 并有默认值，默认行为不劣化。

### S1（P0）链路过载保护 —— 复用已规划方案，不重复设计

**为什么**：基线报告已实测——200/400 并发时 20.9%/89.7% 的请求是 **SSE 中断（客户端放弃）而非服务端主动拒绝**，说明系统过载时「全都在跑、全都慢」，没有快速失败路径；且 `core/infra/postgres.py` 连接池为框架默认（单进程 15），`reranker`/`mcp client` 每次调用新建 httpx 客户端（B2 轮 25 并发下已导致 Qdrant 断路器打开、约 19% 请求静默降级为 BM25）。

**落点**：本项目 `docs/性能验证与链路加固-spec.md` 的 **T1 / T2 / T7** 已给出完整设计（在途信号量 + 快速 503、连接池与 httpx 单例、worker 并发上限），**直接执行那三节即可，不要另起方案**。

**验收**：新增 `tests/unit/test_inflight_limit.py`、`test_http_clients.py`；`pg_stat_activity` 连接数不再随并发线性增长；重跑阶梯加压，对比拐点前后 p95 与 503 率写入报告。

**成本**：3–5 人日。

### S2（P0）缓存击穿保护

**为什么**：客服场景热点问题集中（退货政策/运费谁出）。当前 L1/L2 无 single-flight，同一未缓存问题的并发首访会同时打到 LLM，放大首 token 延迟与上游计费。

**落点**：`docs/性能验证与链路加固-spec.md` **T4**（进程内按 key 合并 + 可选 Redis `SET NX PX`），开关 `SINGLEFLIGHT_ENABLED` 默认开启。

**验收**：并发 20 打同一未缓存问题，日志可证 LLM 调用数显著低于 20；锁超时/不可用直接回原逻辑，不阻塞不报错。

**成本**：2 人日。

### S3（P1）运行时可调参数 + 提示词外置（吸收「配置中心」概念，不引组件）

**为什么**：现在调召回 topk、RRF 权重、CRAG 阈值、RERANK 候选数都必须改 `.env` 重启进程；提示词硬编码在 `agents/tool_agent.py`、`api/chat.py`、`core/rag/engine.py`。这在中台叙事里是「配置中心热更新」，在本项目里就是「调参要重启」。

**落点**：
- 新增 `app/data/runtime_params.json` + `core/runtime_config.py`：**复用 `personas.py` 的 mtime 热加载模式**（已有先例，不引入新模式）；
- 可热更参数白名单：`VECTOR_TOP_K` / `BM25_TOP_K` / `RERANK_TOP_K` / `RRF_VECTOR_WEIGHT` / `RRF_BM25_WEIGHT` / `CRAG_HIGH_THRESHOLD` / `CRAG_LOW_THRESHOLD` / `CONTEXT_TOKEN_BUDGET` / `SEMANTIC_CACHE_SIMILARITY`；
- 提示词模板抽到 `app/data/prompts/*.md`（tool agent system / chat system / no-result 话术），同样 mtime 热加载；
- `GET/PUT /api/v1/admin/runtime-config`（admin），写入前 Pydantic 校验 + 范围约束，非法值回落旧值并 warning（不静默）；
- 读取点包一层「失败回落 Settings 默认值」，保证配置坏掉不拖垮链路。

**验收**：改 JSON 不重启即生效（搜索测试端点可观测 topk 变化）；非法值不生效且有 warning；新增单测覆盖热加载/回落/越界。

**成本**：2 人日。

### S4（P1）可观测闭环：抓取 + 面板 + 告警

**为什么**：`/metrics` 已实现 10 个指标（含 `degradation_total`、`breaker_state`、`rate_limited_total`、`ttft_seconds`），但**没有任何东西去抓它**——指标存在而不产生价值。方案里「Prometheus + Grafana + 告警」这一条概念正确。

**落点**：
- `docker-compose.yml` 增加 `prometheus`（可选 profile，勿默认启用以保护 4G 主机）+ `grafana`；
- `deploy/prometheus.yml`：scrape `backend:8001/metrics`（宿主机开发用 `host.docker.internal:8002` 注释备选）——该配置的写法在仓库历史中已有可复用样本，按现状重写即可；
- `docs/grafana-dashboard.json`：4 个面板（请求/token 速率、TTFT p50/p95、降级率分组件、断路器状态）；
- 告警规则 4 条：`Reranker/Qdrant 断路器 open > 5min`、`degradation_total 5 分钟增速 > N`、`rate_limited_total 突增`、`/metrics 抓取失败`；
- **注意边界**：不要在 `/metrics` 里放 query/用户名等高基数标签（现有实现已遵守，勿破坏）；`/metrics` 单进程有效——多 worker 需 multiprocess 模式，面板注释写清。

**验收**：`docker compose --profile observability up -d` 后面板有数据；人为触发 `FAULT_RERANKER=fail` 能看到降级曲线抬升。

**成本**：1–2 人日。

### S5（P1）异步任务治理（吸收「死信/重试/幂等」语义，不换 MQ）

**为什么**：`enqueue_task` 有 `Retry(max=2)`，但失败任务**没有可查列表、没有重试入口**——等于「有重试机制但没有运维闭环」（方案里说的死信队列就是这个缺口）。同时队列积压不可观测。

**落点**：
- `core/tasks`：`list_failed(limit)` / `requeue_failed(job_id)`（admin 端点 `GET/POST /api/v1/jobs/failed|{id}/retry`），复用 `fetch_job` 的归属校验语义；
- `/metrics` 增 `assistmind_queue_depth{queue}` 与 `assistmind_job_failed_total{task}`（用 RQ `queue.count` + failed registry 长度，旁路采集）；
- 入库幂等键：`ingest_uploaded_document` 增加 `idempotency_key`（`doc_id + 内容哈希`），重复上传不重复计费/不重复写；
- 失败必 `logger.error`（现有约定已满足，勿改）。

**验收**：注入失败任务后能在列表看到并一键重试成功；队列积压指标随入队增长。

**成本**：2 人日。

### S6（P2）会话状态服务端收口（吸收「状态持久化」概念）

**为什么**：当前 `history` 由前端每轮全量回传（`schemas/chat.py`、`frontend/src/api/chat.js`），服务端只做落库回看。后果：①前端漏传/截断即丢上下文；②多实例或断线重连无法恢复；③历史可以被客户端任意伪造（提示词注入面）。方案里的「LangGraph 状态持久化」指向的正是这个问题。

**落点**：
- `POST /chat/ask` 的 `history` 改为**可选降级**：优先按 `conversation_id` 从 PG/Redis 读最近 N 轮（复用 `conversation_service.list_messages`），读不到再回落到请求体 history；
- 记忆窗口裁剪仍归 `core/dialog/trim_history`（已有，勿另写）；
- **不引入 `langgraph-checkpoint-redis`**：当前 Agent 图是单次请求内构建（`agents/base.py::build_graph`），没有跨请求续跑的必要，引入 checkpointer 属于为用而用；
- 顺手把「前端传 history 优先级」写进测试，防止未来悄悄改回。

**验收**：不传 history 也能多轮正确指代（「物流到哪了」能取到上一轮订单号）；伪造 history 不生效；会话恢复（刷新页面）后继续对话行为一致。

**成本**：2–3 人日（含前端小改与回归）。

### S7（P2）CPU 密集侧剥离

**为什么**：这是唯一一条「方案与本项目实测完全同向」的建议。基线报告：50 并发时被测进程 CPU 峰值 343%、RSS 2.4GB，吞吐停在 ~4.5 QPS；本地 reranker 单次 25.2s 直接把 10 并发压到 0.04 QPS。embedding/rerank 是纯 CPU 推理，放在在线进程里就是与 SSE 抢核。

**落点**（按成本递增）：
1. 最省：**进程内限流**——embedding/rerank 用专用 `ThreadPoolExecutor(max_workers=N)`，避免默认线程池被并发请求打满（1 人日）；
2. 中等：**独立 worker 进程**——`embedding-service` 容器暴露 HTTP（`/embed`、`/rerank`），在线侧改为 HTTP 调用 + 超时 + 降级到 BM25/跳过重排（3–4 人日，含降级与测试）；
3. 不建议：直接上 GPU 推理集群（当前无需求）。

**验收**：剥离后在线服务 CPU 峰值下降、p95 改善；embedding 服务不可用时链路自动降级且错误率受控（复用既有降级表语义）。

**成本**：1–4 人日（取决于选哪档）。

---

## 六、明确不做 / 条件触发

| 项 | 判断 | 触发条件（满足再考虑） |
|---|---|---|
| Nacos | 不做 | 出现跨机服务调用且实例地址频繁变化（≈ 多机部署 + 自动扩缩容） |
| RocketMQ / Kafka | 不做 | 单队列日任务量达到万级，或需要跨系统事件总线 |
| Milvus | 不做 | 向量量级 > 千万级，或需要分布式分片与多副本 |
| Elasticsearch | 不做 | 知识库从百 MB 增至 GB 级且 BM25 内存索引不可行；或需要复杂聚合分析 |
| MySQL | 不做（反向） | 无。PG 已满足，且 pgvector 可作为「缩小技术栈」的可选路径 |
| K8s + HPA | 不做 | 多机部署 / SLA > 99.5% / 需要滚动发布 |
| 多 Agent 规划-校验-总结编排 | 不做 | 出现确实需要多轮协商的子任务（当前没有；强行引入会推高 TTFT） |
| 多租户（tenant_id 贯穿 Qdrant/PG/缓存/RBAC） | 不做（但**这是「中台化」的真前置**） | 出现第二家客户/第二条业务线。届时工作量主要在数据模型与隔离，而非换中间件 |
| OpenTelemetry 全链路 | 不做 | Langfuse 的 LLM 侧不足，且需要跨组件因果定位时（配套 collector + Tempo/Jaeger） |
| Loki 集中日志 | 不做 | 多容器日志排查成为日常痛点时 |

**一句提醒**：那份方案里所有「中台」能力（多租户、命名空间隔离、灰度权重、多业务组 group）都依赖一个当前不存在的东西——**tenant / business-line 维度**。没有它，Nacos 的 namespace/group 只是空转；有了它，先用 PG + payload 字段也能做出来。所以「要不要中台」的答案不取决于中间件选型。

---

## 七、对方案文本的几处技术勘误

评估时顺带核对，供参考（不影响主结论）：

1. **「LangGraph graph 对象可序列化，支持存入存储」**——不准确。可持久化的是**运行状态（checkpoint）**，不是编译后的 graph；且 `RedisSaver` 在独立包 `langgraph-checkpoint-redis` 中，本项目当前未安装（LangGraph 1.2.10 默认无持久化）。
2. **「Milvus 支持向量过滤」**被当作差异化优势——Qdrant 的 payload filter 是同级能力，且本项目已用它做 RBAC 与文档启停过滤。
3. **「ES 解决向量检索无法精准匹配专有名词/编号」**——本项目已用 BM25（jieba 词粒度）+ 实体正则（订单号 `20\d{9}`）覆盖该痛点，且知识库仅 147KB。
4. **「RocketMQ 顺序消息：文档分片入库需保证顺序」**——当前入库是单文档内串行 + 写后全量重建 BM25，不存在乱序问题；真正需要保证的是「先 embed 成功再删旧点」这一契约，项目已实现（`core/tasks/__init__.py::_ingest_from_file`）。
5. **「Nacos 权重配置实现灰度发布」**——需要至少两个可路由的实例组；单机单实例部署下该能力不可用。

---

## 八、若决定推进：执行顺序与验证

```powershell
# 每个改动批次完成后必跑（AGENTS.md 约定）
cd backend; venv\Scripts\python.exe -m pytest tests -q -k "not integration"
cd backend; venv\Scripts\python.exe -m ruff check <改动文件>
cd frontend; npm run test:unit        # 涉及前端时
```

批次划分（每批独立可交付、可回退）：

| 批次 | 内容 | 验证证据 |
|---|---|---|
| 1 | S1 + S2（过载保护与击穿保护） | 阶梯加压前后对比表（p95 / 503 率 / 降级率），写入新基线报告 |
| 2 | S3（运行时可调参数与提示词外置） | 不重启改参生效截图 + 单测；RAGAS 分数不回退 |
| 3 | S4 + S5（可观测闭环 + 任务治理） | Grafana 面板截图、故障注入曲线、失败任务重试日志 |
| 4 | S6（会话状态收口） | 多轮指代用例、刷新恢复用例、伪造 history 用例 |
| 5 | S7（CPU 密集侧剥离） | CPU/内存采样前后对比 + 降级验证 |

**回归闸门**（与既有约定一致）：SSE 事件契约不变（只可新增事件名）、既有降级语义不变、缓存按 role/persona 分桶不变、BM25 不可关闭；性能改动必须附前后实测，不能只写「应该更快」。
