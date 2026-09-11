# 性能验证与链路加固 · 编码 Agent 任务书

> **交付对象**：编码 Agent（Cursor / Codex / Claude Code 等）
> **项目**：AssistMind（后端 dev 端口 8002 / Docker 8001，前端 5173）
> **必读前置**：`AGENTS.md`（架构红线，冲突时以它为准）、`docs/测试实操命令手册.md`（现有测试命令）
> **核心执行原则**：**先可观测 → 再出基线 → 后做优化**。禁止无数据驱动的"优化"；禁止改动已有降级语义。

---

## 0. 提示词（直接复制给编码 Agent）

### 提示词 A —— 建可观测 + 压测体系（**第一优先，先跑这个**）

```
任务：为 AssistMind 建立性能可观测能力与压测体系，目标是能产出带拐点的性能基线报告。
必读：docs/性能验证与链路加固-spec.md（全文）、AGENTS.md（架构红线）。

执行范围（只做 T3、T5、T6、T8 与第 3 章压测体系，不做 T1/T2/T4/T7）：
1. T3 TTFT 与阶段耗时埋点
2. T5 应用级 /metrics（Prometheus，python prometheus_client）
3. T6 故障注入开关（env 驱动）
4. 第 3 章全部：tests/load/ 压测体系（Locust 为主）+ Mock LLM 模式 + 场景 S1–S7 + 阶梯 profile

硬约束（违反即返工）：
- 不改动 SSE 事件契约（start/retrieving/rewriting/generating/delta/tool_call/tool_result/done/error），
  新增只能加事件名，不能改已有事件语义与字段。
- 不动既有降级路径、断路器、RRF 截断、缓存按 role 分桶、L1 用 hash + _expires_at 的实现。
- 埋点必须旁路：任何情况下不得阻塞请求、不得抛异常、不改变返回值与异常语义（对齐 Langfuse 旁路设计）。
- 不开 Prometheus 时不得报错（未配置即整体 no-op）。
- 新增依赖需写进 backend/pyproject.toml，不用全局 pip install。

交付：可运行的 tests/load/ + 一份 docs/perf-baseline-<日期>.md 基线报告（含拐点、p95/TTFT、资源占用、降级触发统计）。
验证：贴出实际命令与输出，附测试通过截图或日志；pytest -k "not integration" 必须全绿且 coverage ≥ 70%。
```

### 提示词 B —— 按基线数据加固链路（**必须在 A 之后**）

```
任务：依据 docs/perf-baseline-<日期>.md 的实测拐点，加固 AssistMind 的并发与稳定性链路。
必读：docs/性能验证与链路加固-spec.md 第 1.2 节（缺口 G1–G8）与第 2 章（T1/T2/T4/T7）。

执行范围：T1 在途并发控制、T2 连接池与 HTTP 客户端复用、T4 缓存击穿保护、T7 worker 并发上限。

硬约束：
- 参数必须来自配置项（settings），不得硬编码魔数；默认值必须保持现有行为不劣化。
- 超限拒绝要走统一错误契约（503 + 明确 detail），不得静默丢弃、不得静默 pass。
- 每个改动点必须配单测（见各任务的"必须补的单测"）。
- 不得为了通过压测而关闭重排、关闭 BM25、放宽限流、放宽超时。
- 提交前跑：pytest -k "not integration" 全绿；再复跑压测对比拐点前后差异，把 diff 写进报告。
```

### 提示词 C —— 只跑一次基线（不改任何业务代码）

```
任务：在不修改 backend/app 业务代码的前提下，用 tests/load/ 跑一轮性能基线。
前置：把 RERANKER_PROVIDER=siliconflow、LLM_PROVIDER=deepseek（见 spec 第 1.2 节 G8，
默认 local/ollama 组合会让压测测到本机 CPU 而不是系统能力），并把配置写进报告。
输出：docs/perf-baseline-<日期>.md，含：
  1) 环境与配置快照（CPU/内存/容器版本/关键 env）
  2) 阶梯加压表：并发 10/25/50/100/200/400 → QPS、p50/p95/p99、TTFT、错误率、降级触发数
  3) 拐点判定结论（吞吐不再上升且 p95 陡增的位置）
  4) 四类指标的原始 CSV 落盘路径
  5) 未验证项与风险（不要粉饰）
```

> **顺序不可颠倒的理由**：没有 T3/T5 的指标，压测只能得到"慢"这个结论，无法定位是哪一层慢；先补可观测再压测，才能让 T1/T2/T4/T7 变成**数据驱动**的改动，而不是猜。

---

## 1. 现状事实核对（先读，避免改错地方）

### 1.1 已经做了、**禁止重复实现或擅自改动**

| 能力 | 位置 | 结论 |
|---|---|---|
| LLM 重试 + 指数退避 + 抖动 | `core/infra/llm_factory.py:88-119`，`wait_exponential_jitter`，`LLM_MAX_RETRIES=2` | **已有**。不要再"加退避"。真正的问题是重试放大没有全局配额（见 G1） |
| 断路器（aiobreaker） | `call_with_breaker`，reranker 与 LLM 共用 | **已有**，勿替换 |
| Reranker 硬超时护栏 | `reranker.py:139` `asyncio.wait_for(..., RERANKER_TIMEOUT+5)` | **已有**，保留（httpx timeout 曾失效拖死 SSE） |
| 全局限流 | `core/infra/rate_limit.py`，Redis 固定窗口，Redis 故障时 **fail-open 是设计决策**（注释已写明） | **已有**。不要擅自改成 fail-closed（见 T8 的约束） |
| 语义缓存按 role/persona 分桶 | `semantic_cache.py:_bucket` | **禁止合桶**（RBAC 安全） |
| L1 用 hash + `_expires_at` 控 TTL | `semantic_cache.py:145-147` | **禁止**用 `redis.set` 同 key 写 string（会 WRONGTYPE 覆盖 hash，历史 bug） |
| fast=True 快速失败链路 | `llm_factory`，辅助环节 10s+6s 无重试 | **已有**，勿统一成标准超时 |
| BM25 一等公民 / RRF / `RERANK_TOP_K` 截断 | `core/rag/engine.py:157-162` | **已有**，勿改成返回全量 reranked |
| SSE 事件契约 | `api/chat.py` | **契约冻结**：只能加事件，不能改语义或字段 |
| 质量回归体系 | RAGAS 65 条双数据集、坏例回流 | **已有**，压测后必须一并复跑（性能与质量双闸门） |

### 1.2 确认存在的缺口（本次要处理，附证据）

| 编号 | 缺口 | 证据 | 影响 |
|---|---|---|---|
| **G1** | **无在途请求 / 并发槽上限** | 全仓检索无 `asyncio.Semaphore`；`api/chat.py` 直接进业务 | SSE 长连接（3–30s）长时间占槽，并发一超直接排队，无排队超时兜底 |
| **G2** | **DB 连接池用框架默认值** | `core/infra/postgres.py:15`：`create_async_engine(url, echo=..., pool_pre_ping=True)` 未传 `pool_size` / `max_overflow` → SQLAlchemy 默认 5 + 10 = **单进程 15 连接上限** | 与知识库里"连接池耗尽"场景完全同构。压测中会先于 CPU 饱和 |
| **G3** | **httpx 客户端未复用** | `core/rag/reranker.py:105` 每次调用 `async with httpx.AsyncClient(...)`；`mcp/client.py:80` 同类写法 | 每次请求新建连接池，无 keep-alive，TLS/连接开销重复付，且连接数无统一管控 |
| **G4** | **无 TTFT 埋点** | `llm_factory` 只有 `duration_ms`（全长），无首 token 时刻 | 流式体验的核心指标不可度量，"优化 TTFT"无法验证 |
| **G5** | **无缓存击穿保护** | `semantic_cache.py` 无 single-flight、无 `SETNX` 互斥 | 热点问题（如"退货政策"）并发首访同时 miss → 同时打 LLM，瞬间放大 |
| **G6** | **应用自身无 Prometheus 指标** | 无 `prometheus_client`；`api/ops.py:205` 的 `/ops/metrics/...` 是业务查询接口，**不是 exporter** | 有 Prometheus 容器却无应用指标可采，压测只能看外部黑盒数据 |
| **G7** | **RQ worker 无并发上限** | `scripts/run_worker.py` | 重任务（知识库重建 / 65 条 RAGAS 评估）可与在线请求抢 CPU / 内存 |
| **G8** | **实际运行配置是低吞吐组合（最重要）** | `config.py:40` `RERANKER_PROVIDER: str = "local"` 且全仓无覆盖；`tests/conftest.py:121-122` 明确"本机 .env 已切 `LLM_PROVIDER=ollama`" | **默认即"本机 CPU 重排（单次 1–2 分钟）+ 本机 Ollama 推理"**。不先切云端，压测测的是本机 CPU 而不是系统架构能力，结论无意义 |

> **G8 的处理方式**：不是改默认值（会破坏离线可跑性），而是**在压测 profile 与报告里显式声明配置**，并跑两组对比：本地组（证明瓶颈层）vs 云端组（测系统真实容量）。这组对比是容量结论的直接证据。

### 1.3 红线（违反即返工）

- 后端全异步，不得引入同步阻塞调用（`time.sleep`、同步 `requests`、同步 DB 驱动）
- 不用 SQLite / aiosqlite；不加 TypeScript
- 失败降级不可静默：至少 `logger.warning`，且必须走既有降级表
- `pytest -k "not integration"` 全绿 + `--cov-fail-under=70` 是硬门禁

---

## 2. 任务清单

### T1 在途并发控制与排队兜底（P1，核心加固）

**背景**：G1。SSE 请求驻留 3–30s，是典型的长占用请求；没有并发槽上限，系统在超载时会"全都在跑、全都慢"，而不是"一部分被快速拒绝"。后者对用户和上游都更友好。

**改动点**
- 新增 `core/infra/concurrency.py`：进程级 `asyncio.Semaphore(settings.MAX_INFLIGHT_REQUESTS)`
- 在 `api/chat.py` 与 `/ops/diagnose` 入口获取槽位；获取超时 `INFLIGHT_ACQUIRE_TIMEOUT`（默认 5s）后返回 503 + `Retry-After`
- 新增配置：`MAX_INFLIGHT_REQUESTS`（默认 50）、`INFLIGHT_ACQUIRE_TIMEOUT`（默认 5）
- 槽位占用/排队深度需暴露给 T5 的指标

**验收标准**
- 超过上限时行为是**快速 503**，不是无限排队；SSE 已开始的事件流不受影响（已发出的 `delta` 不中断）
- 默认值下现有单测与集成行为不变化（并发 1 时与改动前一致）
- 配置设 0 或负数时告警一次并整体停用（对齐限流中间件的处理哲学）

**必须补的单测**：`tests/unit/test_inflight_limit.py` —— ① 并发 N 不超限；② 超限快速 503；③ 超时后能继续服务；④ 配置非正值时告警且放行

**禁止做法**：不要用全局信号量卡住非 LLM 接口（health / mcp 必须不受影响）；不要改 SSE 事件契约。

### T2 连接池与 HTTP 客户端容量对齐（P1）

**背景**：G2 + G3。连接池默认 15、HTTP 客户端每次新建——两者都是"并发一上来就先爆"的地方。

**改动点**
- `postgres.py`：显式传 `pool_size` / `max_overflow` / `pool_timeout`，值来自配置；并在启动日志打印实际生效值
- httpx 客户端统一为**模块级单例**（reranker / mcp client / embedding / prometheus / elasticsearch / alertmanager 一致性处理），显式配置 `limits=httpx.Limits(max_connections=..., max_keepalive_connections=...)`、`timeout`、`http2` 按需
- 生命周期：单例的创建与关闭挂在 FastAPI lifespan（`main.py`），不要每请求创建

**验收标准**
- 启动日志可见连接池参数；`pg_stat_activity` 中连接数不再随并发线性增长
- 压测前后对比：reranker 路径的 p95 与连接相关错误率下降
- 客户端关闭有明确生命周期，不留 pending task warning

**必须补的单测**：`tests/unit/test_http_clients.py` —— 单例复用（两次调用同一对象）、lifespan 关闭后不报错、连接池参数来自配置

**禁止做法**：不要把单例塞进模块导入时就发起网络连接（延迟到首次使用或 lifespan）。

### T3 TTFT 与阶段耗时埋点（P0，压测前提）

**背景**：G4。没有 TTFT 就无法验证"流式到底快了多少"，也无法比较不同配置。

**改动点**
- 在 `api/chat.py` 的 SSE 生成器里记录：`t0`（请求进入）→ `t_first_delta`（第一个 `delta` 写出）→ `t_done`
- 阶段耗时：意图路由 / 查询改写 / 检索 / 重排 / CRAG / 生成 各段
- 落点：Langfuse span（既有旁路机制）+ 结构化日志（`extra` 字段），便于压测脚本解析
- 不新增同步成本；`is_langfuse_enabled()` 为 False 时仍要有日志

**验收标准**
- 日志中可稳定拿到 `ttft_ms` 与各阶段 `*_ms`
- 埋点异常被吞掉且 `logger.debug`，不影响请求（旁路性验收：mock 掉 Langfuse 与 logger 后请求仍成功）
- 输出字段命名统一为 `*_ms` 整数

**必须补的单测**：`tests/unit/test_ttft_metrics.py` —— ① 正常链路产出 ttft_ms；② 埋点抛异常时请求不受影响；③ 降级路径（Reranker 跳排）仍能产出耗时

### T4 缓存击穿保护（P2）

**背景**：G5。客服场景热点问题集中，并发首访会同时 miss。

**改动点**
- 方案 A（推荐，单实例）：进程内按 `l1_key` 的 `asyncio.Lock` 合并同 key 请求（single-flight）
- 方案 B（多副本）：Redis `SET NX PX` 短锁 + 短等待 + 兜底直接生成（拿不到锁时不要死等）
- 提供 `SINGLEFLIGHT_ENABLED` 开关，默认开启；锁超时时间须短于 LLM 超时预算

**验收标准**
- 并发 20 打同一未缓存问题：打向 LLM 的请求数应显著低于 20（日志可验证）
- 锁不可用/超时 → 直接走原逻辑，不阻塞、不报错
- 不影响 L1/L2 命中路径的现有单测

**必须补的单测**：`tests/unit/test_singleflight.py` —— ① 并发同 key 只生成一次；② 锁超时后正常降级；③ 不同 key 不互相阻塞；④ 关闭开关后行为回到现状

### T5 应用级 Prometheus 指标（P0，压测前提）

**背景**：G6。项目已有 `prometheus` + `ops-exporter` 容器，但应用自身没有 exporter。

**改动点**
- 引入 `prometheus_client`，新增 `GET /metrics`（**不在** `/api/v1` 下，或按现有约定挂载；不与 `/api/v1/health` 冲突）
- 指标清单（命名 `assistmind_*`）：

| 指标 | 类型 | 标签 |
|---|---|---|
| `assistmind_inflight_requests` | Gauge | — |
| `assistmind_inflight_rejected_total` | Counter | reason |
| `assistmind_request_duration_seconds` | Histogram | intent（faq/task/chat/unclear） |
| `assistmind_ttft_seconds` | Histogram | intent |
| `assistmind_stage_duration_seconds` | Histogram | stage（rewrite/retrieve/rerank/generate） |
| `assistmind_cache_hit_total` | Counter | level（l1/l2/miss）、role |
| `assistmind_degradation_total` | Counter | component、reason |
| `assistmind_breaker_state` | Gauge | component |
| `assistmind_llm_upstream_errors_total` | Counter | provider、status |
| `assistmind_rate_limited_total` | Counter | scope |

- 加进 `docker-compose.yml` 的 Prometheus scrape 配置；不开 Prometheus 时应用侧能力不受影响

**验收标准**
- `curl localhost:8002/metrics` 返回上述指标，且在压测中数值随之变化
- 单实例多进程部署时指标不丢失（说明是否用 multiprocess 模式，若不用需在文档写明"仅单进程有效"）
- 指标采集本身不引入可测量的延迟（对比压测前后 p95）

**禁止做法**：不要把用户名/token/query 原文放进标签（高基数 + 信息泄漏）。

### T6 故障注入开关（P0，压测前提）

**背景**：第四部分要求验证"降级是否正确生效"，需要可控、可复现的故障注入。

**改动点**（env 驱动，默认全关，配置文件写明）
- `FAULT_LLM=ok|timeout|error|slow`：控制 LLM 层行为
- `FAULT_RERANKER=ok|timeout|fail`
- `FAULT_QDRANT=ok|down`
- 实现位置：复用既有 provider/断路器抽象，**不要**在业务路径里散落 if

**验收标准**
- 每个故障模式下，全链路降级表对应的路径被触发且 `logger.warning` 可见
- `assistmind_degradation_total` 对应标签 +1
- 故障关闭后能自动恢复到正常路径（不需要重启）

**必须补的单测**：每个故障模式至少 1 条（断言"降级生效 + 有 warning + 不抛异常"）

### T7 RQ worker 并发上限（P2）

**背景**：G7。重任务会与在线请求抢资源。

**改动点**
- 限制 worker 并发消费数；重任务（`rebuild_knowledge_base` / `run_evaluation`）串行或低并发
- 采集队列积压量与 job 等待时长（接 T5）

**验收标准**
- 同时入队多个重任务时，不出现 CPU 打满导致在线 p95 明显劣化
- 队列积压可观测

### T8 Redis 故障下限流兜底（P2，可选，**不得改变默认行为**）

**背景**：`rate_limit.py` 明确设计为 fail-open（"宁可短暂无防护，不可让用户不可用"）。风险是：Redis 挂掉时**缓存失效（回源放大）与限流失效（无保护）同时发生**。

**改动点**（仅在显式开启时生效）
- 新增 `RATE_LIMIT_FALLBACK=off|inproc`：`inproc` 时使用进程内令牌桶兜底
- 默认 `off`，保持现有 fail-open 语义不变

**验收标准**
- `off` 时行为与当前完全一致（现有 `tests/unit/test_rate_limit.py` 全绿）
- `inproc` 时 Redis 挂掉仍能限制单实例请求速率，且有 warning

---

## 3. 压测体系（成熟方案，不重新造轮子）

### 3.1 选型对比

| 方案 | SSE 支持 | 断言/门禁 | 分布式 | 报告 | 适合本项目的点 |
|---|---|---|---|---|---|
| **Locust 2.x**（推荐） | 需手写（`requests` + `stream=True`，见 3.4） | Python 代码即断言，可写 SLO | 原生 `--master/--worker` | 自带 `--html` + CSV | **Python 同栈**，团队可维护；并发用户数语义天然对齐"并发 SSE 连接" |
| **k6** | 需 `xk6-sse` 扩展（`xk6 build --with github.com/phymbert/xk6-sse`） | 原生 `thresholds`（最适合 CI 门禁） | k6 Operator（K8s，本场景过重） | `handleSummary` → JSON/HTML | 门禁最省事；但需自建二进制，维护成本高 |
| JMeter | 有 SSE Sampler | JSR223 断言 | 原生分布式 | HTML Dashboard | GUI 上手快，但脚本难评审、CI 不友好 |
| hey / wrk / vegeta | 不支持 | 无 | 简单负载 | 简单 | 只用于静态接口（如 `/health`、`/metrics`）冒烟 |

**推荐组合**：**Locust 为主**（场景编排 + 混合流量 + SSE/TTFT 采集），**k6 或简单脚本做 CI 门禁冒烟**，`hey` 打静态接口做基准，`Prometheus + Grafana` 做实时观测（容器已有），`Toxiproxy` 做故障注入。

### 3.2 目录结构

```
backend/tests/load/
├── README.md                 # 怎么跑、指标口径、报告在哪
├── locustfile.py             # 入口，注册场景
├── scenarios/
│   ├── s1_cache_hit.py       # 高频重复问句
│   ├── s2_faq_miss.py        # 完整 RAG 链路
│   ├── s3_task_agent.py      # Agent 多轮工具链
│   ├── s4_mixed.py           # 混合流量（默认场景，权重 5:3:2）
│   ├── s5_fault_injection.py # 故障注入（配 T6）
│   ├── s6_spike.py           # 尖峰
│   └── s7_soak.py            # 浸泡
├── lib/
│   ├── sse_client.py         # SSE 请求 + TTFT 采集
│   ├── auth.py               # 登录拿 JWT 并复用
│   └── ingest.py             # 采集结果落盘 CSV
├── profiles/
│   ├── smoke.conf            # CI 门禁：1–2 分钟小负载
│   ├── baseline.conf         # 阶梯加压找拐点
│   ├── stress.conf           # 超载验证（触发 503/429）
│   └── soak.conf             # 30–60 分钟
└── corpus/
    └── README.md             # 语料来源说明
```

### 3.3 压测语料（复用已有资产，不要另造数据）

**语料直接用项目已有的 65 条评估集**：`app/data/eval_qa.json`（25 条，含 4 条对抗）+ `app/data/eval_mall_qa.json`（40 条，含 5 条对抗），再补一组高频 FAQ（退货政策 / 运费谁出 / 优惠券叠加）用于 S1 缓存命中场景。

**这个选择的双重价值**：① 请求语义真实，不是 `test-1-2-3` 这种假数据；② 压测后可顺手复跑 RAGAS，验证"压力下触发的降级是否拖低了答案质量"——**性能与质量双闸门**。

### 3.4 SSE 与 TTFT 采集方法（关键实现）

Locust 是同步模型，用 `requests` 流式读取，逐行解析 SSE：

```
POST /api/v1/chat/ask  (headers: Authorization, Accept: text/event-stream)
  t0 = now
  with requests.post(..., stream=True, timeout=...) as resp:
      for raw_line in resp.iter_lines(chunk_size=1):   # chunk_size=1 避免缓冲吞掉首包
          if raw_line.startswith(b"event: delta"):
              if ttft is None: ttft = now - t0        # 首个 delta 即 TTFT
          if raw_line.startswith(b"event: done"):
              break
  上报：ttft_ms、total_ms、事件序列、错误事件
```

**三个必须注意的坑**
1. `chunk_size=1`：不设会被 urllib3 缓冲，TTFT 被虚高。
2. gevent 下确认 `requests` 已被 monkey patch（Locust 默认会 patch），否则流式阻塞会拖死 worker。
3. 压测机与被测机分离；单机场景下 Locust 自身限 1–2 个 worker 进程，并检查 `ulimit -n`（SSE 是长连接，文件描述符很快耗尽）。

**并发用户数的语义**：Locust 的 `users` = 并发 SSE 连接数 = 我们说的"并发槽占用数"，两边口径一致，报告里可直接引用。

### 3.5 Mock LLM 模式（离线压测的关键）

新增 `LLM_PROVIDER=mock`：按固定 token 数与固定间隔流式返回（例如 300 token × 20ms ≈ 6s）。

**用途**：隔离上游，测出**纯服务端能力**（并发槽、连接池、检索、重排、SSE 写出）。这是"离线压测"的定义性能力——没有它，压测结果会混入上游限流，窗口期不同结果不可比。

### 3.6 故障注入（成熟工具）

| 层次 | 工具 | 用法 |
|---|---|---|
| 应用层 | T6 的 env 开关 | `FAULT_LLM=timeout` 等，验证降级路径 |
| 容器层 | **Pumba**（`docker run --rm -v /var/run/docker.sock:/var/run/docker.sock gaiaadm/pumba pause/kill <container>`） | 停/暂停 Redis、Qdrant、PostgreSQL |
| 网络层 | **Toxiproxy**（`ghcr.io/shopify/toxiproxy`，在容器前加代理） | 注入延迟、超时、断连、限带宽，验证超时护栏与断路器 |

验收：每个故障场景下，**降级生效 + 有 warning + 错误率受控 + 自动恢复**，四件事都要在报告里给出证据。

### 3.7 场景与权重

| 场景 | 内容 | 权重参考 | 观察重点 |
|---|---|---|---|
| S1 | 高频重复问句（缓存命中） | 50% | L1/L2 命中率、TTFT、能否免检索免生成 |
| S2 | FAQ 未命中（完整 RAG） | 30% | 检索/重排耗时、p95 |
| S3 | Agent 多轮工具链 | 20% | 单请求驻留时间、ReAct 轮数、MCP 调用 |
| S4 | 混合（= S1+S2+S3 按权重） | 默认场景 | **整体 SLO** |
| S5 | 故障注入 | 独立跑 | 降级正确性 |
| S6 | 尖峰（3 分钟内 ×5） | 独立跑 | 限流是否触发、是否出现排队雪崩 |
| S7 | 浸泡（稳态 30–60 min） | 独立跑 | 内存/连接/缓存泄漏 |

### 3.8 阶梯加压 profile（找拐点）

按 `users` 阶梯上升，每阶稳态保持 >= 3 分钟（等待延迟指标收敛）：

```
10 → 25 → 50 → 100 → 200 → 400    每阶 3 分钟，spawn_rate 5/s
```

判读：**吞吐不再上升 + p95 开始陡增**的位置即拐点。同时对比"实例数 ×2 时吞吐是否近线性"——不线性即存在共享瓶颈（Redis / DB / 上游配额）。

### 3.9 指标口径（必须与 T5 指标一致，避免两套口径）

| 类别 | 指标 | 采集点 |
|---|---|---|
| 吞吐 | QPS（分场景）、峰值小时请求量 | Locust |
| 延迟 | p50/p95/p99 总时长、**TTFT**、排队等待时长 | Locust + `assistmind_ttft_seconds` |
| 稳定性 | 错误率（拆 4xx/429/503/超时）、SSE 中断率、降级触发数、断路器开合、队列积压 | Locust + `assistmind_*` |
| 资源与成本 | CPU/内存/协程数、DB 连接 active&waiting、Redis 连接数、上游 RPM/TPM 与 429 率、每千次问答 token 成本 | Prometheus + Grafana + PG 侧查询 |

### 3.10 CI 门禁

- `profiles/smoke.conf`（1–2 分钟、小负载）纳入 CI，作为**性能回归**：对比上次基线的 p95/TTFT，劣化超阈值即失败
- 与现有 CI 的关系：RAGAS 守住"答得对不对"，smoke 压测守住"快不快、稳不稳"，两者并列，都不阻塞开发环境的常规单测

---

## 4. 验收标准（Definition of Done）

- [ ] `pytest tests -q -k "not integration"` 全绿，`--cov=app --cov-fail-under=70` 通过
- [ ] `curl localhost:8002/metrics` 返回全部约定指标且压测中数值变化
- [ ] 日志可稳定获取 `ttft_ms` 与各阶段耗时
- [ ] `tests/load/` 可按 README 一条命令跑通 S1–S7
- [ ] 产出 `docs/perf-baseline-<日期>.md`，含拐点结论、四类指标、故障注入证据、未验证项
- [ ] 未破坏任何既有降级语义与 SSE 契约（回归：既有单测全绿 + 手动跑一次"我要退货"全链路）
- [ ] 所有新增参数进配置文件并有默认值，新增依赖进 `pyproject.toml`

---

## 5. 执行顺序与依赖

| 阶段 | 内容 | 依赖 | 产出 |
|---|---|---|---|
| P0 | T3 + T5 + T6，Mock LLM 模式 | 无 | 有指标可看 |
| P1 | 3.2–3.8 压测体系 | P0 | 能跑阶梯加压 |
| P2 | 跑基线（**先切 `RERANKER_PROVIDER=siliconflow` + `LLM_PROVIDER=deepseek`**，并跑一组本地对比） | P1 | `perf-baseline-*.md` + 拐点 |
| P3 | T1 + T2 + T4 + T7（按 P2 数据定优先级） | P2 | 拐点右移的可证改进 |
| P4 | T8（可选）、smoke 门禁进 CI | P3 | 防回退 |

> **不要跳 P0/P2 直接做 P3**：没有基线的"加固"无法证明有效，也无法回答"改完好了多少"。

---

## 6. 交付物清单

1. `backend/app/core/infra/concurrency.py`（T1）
2. `backend/app/core/infra/postgres.py` 与各 httpx 客户端单例化（T2）
3. `backend/app/api/chat.py` 埋点（T3，不改契约）
4. single-flight 实现（T4）
5. `GET /metrics` + 指标注册（T5）+ `docker-compose.yml` scrape 配置
6. 故障注入开关（T6）
7. `scripts/run_worker.py` 并发上限（T7）
8. `backend/tests/load/` 全套（locustfile + 场景 + profiles + 语料说明 + README）
9. `docs/perf-baseline-<日期>.md` 基线报告
10. 新增单测：`test_inflight_limit.py` / `test_http_clients.py` / `test_ttft_metrics.py` / `test_singleflight.py` / `test_fault_injection.py`
11. `docs/grafana-dashboard.json`（可选，观测面板）

---

## 7. Agent 自检命令（提交前必须跑）

```powershell
# 1. 单测（不需要 Docker）
cd backend; venv\Scripts\python.exe -m pytest tests -q -k "not integration"

# 2. 覆盖率门禁
venv\Scripts\python.exe -m pytest tests -q -k "not integration" --cov=app --cov-fail-under=70

# 3. 指标端点
curl http://localhost:8002/metrics

# 4. 压测冒烟
cd backend\tests\load; locust -f locustfile.py --headless -u 20 -r 5 -t 60s --html smoke.html

# 5. 阶梯加压（基线）
locust -f locustfile.py --headless --config profiles/baseline.conf --html baseline.html --csv baseline

# 6. 质量未回退（压测后复跑，与性能结论并列写入报告）
cd backend; venv\Scripts\python.exe scripts/run_eval.py
```

**报告里必须写清楚的三件事**：① 配置快照（含 G8 的 reranker/LLM provider）；② 拐点在哪、由哪一层决定；③ 哪些结论是实测、哪些仍是估算（不要把估算写成实测）。
