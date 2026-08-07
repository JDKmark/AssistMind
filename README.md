# AssistMind —— 电商智能客服系统

> 面试作品集级 RAG + Agent 智能客服系统，覆盖**电商客服问答**（商品咨询 / 订单查询 / 物流查询 / 售后申请）与**智能运维诊断**两大场景。
> 产品定位是"智能客服系统"（非商城系统）：知识库素材取自开源项目 macrozheng/mall 的文档与表结构（详见[知识来源与演示口径](#五知识来源与演示口径诚实透明)），业务演示数据为项目自建。

## 一、项目亮点速览

- **RAG 混合检索**：Qdrant 向量召回 + BM25 关键词召回（jieba 词粒度）双路并行，RRF 融合（权重可配），Reranker 精排
- **查询改写**：Multi-Query 默认启用（3 变体并行召回）+ HyDE 可选 + CRAG 低分被动改写
- **结构感知切块**：Markdown 标题 / fenced 代码块整体保留 / 表格不拆行 / mall.sql 按 CREATE TABLE 表级切块
- **Agent 业务闭环**：ToolAgent（ReAct）经 MCP 调用工具完成订单校验 → 售后申请 → 工单创建，全链路可流式可视化
- **MCP 双向架构**：FastMCP 实现 Server（13 个工具）+ Client，ToolAgent 与工具实现解耦
- **三级意图路由**：规则 → 语义 → LLM，5 类意图（faq / task / chat / diagnose / unclear），配置外置热加载
- **失败降级体系**：断路器 + 全链路降级表，BM25 为一等公民（Qdrant 挂掉仍可答）
- **智能运维诊断**（扩展能力）：Supervisor Agent 多源证据编排 + SSE 实时诊断流 + 自动建故障工单
- **可观测性**：Langfuse 全链路 trace（LLM 单点埋点 + 诊断链路编排）
- **评估体系**：RAGAS 4 指标（39 条数据集含 6 条对抗样本）+ 诊断根因命中率 3/3
- **CI/CD**：GitHub Actions（ruff + pytest + vitest）+ husky pre-commit

## 二、核心场景（面试主叙事）

### 2.1 电商客服：RAG 问答 + Agent 工具调用

面向电商客服的两类典型问题，系统按意图自动分流：

| 场景 | 用户问题示例 | 处理链路 |
|---|---|---|
| 商品 / 政策咨询 | "运费谁出？""优惠券能叠加吗？""退货政策是什么？" | faq 意图 → RAG 检索（向量 + BM25 + RRF + Rerank + CRAG）→ 基于知识库生成 |
| 订单 / 物流查询 | "查一下我的订单""物流到哪了？" | task 意图 → ToolAgent → MCP 调 `query_order` / `query_logistics` |
| 售后申请 | "我要退货""我要申请售后" | task 意图 → ToolAgent → `query_order` 校验状态 → `apply_refund` 创建售后单 → 生成工单 |
| 转人工 / 建工单 | "帮我转人工""提交一个工单" | task 意图 → ToolAgent → `transfer_human` / `create_ticket` |

前端以 SSE 流式呈现全过程：意图分流标签 → 查询改写提示 → 工具调用步骤（工具名 + 参数 + 结果）→ 知识来源引用 → 最终答案。

### 2.2 Agent 业务闭环示例：「我要退货」

一条完整的业务闭环（story，可在 Chat 页面实测复现）：

> 用户输入"我要退货" →
> 1. **意图路由**：命中 task 意图（关键词 + 语义样本命中，不消耗 LLM）；
> 2. **ToolAgent 决策**：ReAct 循环中先按"Retrieval Before Agency"原则检索售后政策知识（确认退货前提：订单已发货/已完成、7 天无理由等），再决定动作；
> 3. **校验订单状态**：调用 `query_order("20240801001")`，返回订单状态"已发货"（可售后），如实转述，不编造；
> 4. **创建售后单**：调用 `apply_refund(order_sn, reason)`，数据源校验状态并创建售后单（`refund_id=AF{order_sn}`，重复申请幂等返回已存在记录）；
> 5. **生成工单**：按需调用 `create_ticket` 沉淀售后/问题工单，与用户核对处理时效；
> 6. **告知结果**：结构化输出售后单号、当前状态（处理中）与后续流程。

每一步的工具调用都以 `tool_call` / `tool_result` SSE 事件实时推送到前端，用户可见"正在查订单 → 正在申请售后 → 已创建售后工单"的过程——这也是面试中展示"Agent 不是黑盒"的现场素材。

> 说明：订单 / 物流 / 售后数据为项目自建演示清单，默认走内存 mock（零依赖）；配置 `MALL_DATA_SOURCE=real` 后可落 PostgreSQL 持久化（含售后单跨进程幂等），详见[五、快速启动](#五快速启动完整闭环)。

### 2.3 扩展能力：智能运维诊断（系统底座能力的延伸）

电商客服对话中混入的故障类问题（如"下单失败了，帮我诊断"）会走 diagnose 意图，交由 **OpsSupervisorAgent** 处理——这是系统底座（RAG + Agent + MCP + 可观测）能力的延伸演示，不占据产品主叙事：

> 用户报"下单失败" →
> 1. 意图路由识别为 diagnose → 进入 SSE 诊断流（`/api/v1/ops/diagnose`：start → planning → collecting → analyzing → incident → done）；
> 2. **Supervisor Agent 多源证据编排**（LangGraph 三节点：supervisor → collect → analyze）：并行采集指标（Prometheus）/ 日志（ELK 可选）/ 变更记录 / 告警 / 知识库手册，并关联**相似历史工单**（模糊检索）与**受影响服务的主机拓扑**（CMDB 风格 `list_hosts`）；
> 3. **根因推理**：LLM 基于证据链生成根因报告 + 置信度评估，逐证据项标注 degraded 降级状态；
> 4. **自动建故障工单**：报告产出根因后自动创建 incident 类工单（可在开关中关闭）。

运维数据源为受控演示环境（预置 3 个故障场景 + mock/real 双数据源），详见[知识来源与演示口径](#六知识来源与演示口径诚实透明)。

## 三、技术底座（「怎么做」五层）

### 3.1 RAG 链路

```
用户问题
  → 查询改写（Multi-Query 3 变体，HyDE 可选）
  → 混合检索：Qdrant 向量（bge-base-zh-v1.5, 768 维）+ BM25（jieba 词粒度, 一等公民）
  → RRF 融合（Reciprocal Rank Fusion，向量/BM25 权重可配，默认等权）
  → Reranker 精排（bge-reranker-v2-m3）
  → CRAG 相关性评估（高阈值直接生成 / 低阈值重检索 / 兜底）
  → 语义缓存（Redis 版本号 INCR 失效 O(1) + 惰性清理，L2 相似度判定）
  → 生成（DeepSeek 主 / Ollama 备）
```

- **结构感知切块**：`chunk_text` 识别 Markdown 标题（→ section_title）、fenced 代码块整体保留（允许超限）、表格不拆行；`mall_tables.sql` 按 CREATE TABLE 语句切块（→ table_comment）；取材说明类文档（SOURCES.md）不入库。
- **意图路由前置**：RAG 只服务 faq 意图，task / diagnose 走 Agent，避免无谓检索。
- **RBAC**：Qdrant payload 按 `security_group` 字段过滤，不建独立权限表。

### 3.2 Agent

- **ToolAgent（电商客服 Agent）**：LangGraph StateGraph 驱动的 ReAct 循环（think → act → …），含 Loop Breaker（迭代上限熔断）、参数缺失澄清（`CLARIFY:` 追问）、**实体识别参数补填**（规则抽取订单号/商品 ID → 自动补填 query_order 等工具参数，减少 LLM 编造与追问，多轮对话从历史回溯实体）、MCP 不可用降级。设计原则 **Retrieval Before Agency**：Agent 在已排序检索结果之上工作，不取代检索。
- **OpsSupervisorAgent（运维诊断 Agent）**：LangGraph 三节点编排（supervisor 规划 → collect 多源证据采集 → analyze 根因推理），输出根因报告 + 置信度 + 证据清单（含 degraded 标注），联动自动建单。

### 3.3 MCP 双向架构

FastMCP（Python 官方 SDK）实现 **Server（AssistOps，13 个工具）+ Client**，ToolAgent 不直接调本地工具函数，一律通过 MCP Client → Server 协议调用，工具实现与 Agent 完全解耦：

| 分组 | 工具 |
|---|---|
| 知识检索（1） | `search_knowledge` |
| 电商业务操作（4） | `query_order` / `query_logistics` / `query_product` / `apply_refund` |
| 运维数据（4） | `query_metric` / `search_log` / `query_change` / `get_alerts` |
| 工单（4） | `create_ticket` / `create_incident` / `transfer_human` / `get_ticket_status` |

### 3.4 降级体系（全链路降级表）

每个外部调用失败都有明确降级路径（断路器 aiobreaker + 超时 + 重试），不可静默失败：

| 组件失败 | 降级策略 |
|---|---|
| LLM 调用失败 | 重试 → 切 Ollama → 缓存近似 → 模板兜底 |
| Qdrant 失败 | 仅 BM25 召回（BM25 不可被关闭） |
| BM25 失败 | 仅向量召回 |
| 两路召回均失败 | 返回"未找到相关文档" + 建议转人工 |
| Reranker 失败 | 跳过重排，用 RRF 结果 |
| Redis 缓存失败 | 跳过缓存直查 |
| PostgreSQL 失败 | 工单类返回 503，聊天类不受影响 |
| Prometheus 失败 | 指标返回空 + degraded；auto 模式下整体降级 mock |
| Elasticsearch / Alertmanager 失败 | 日志/变更/告警返回空 + degraded |

### 3.5 可观测性

- Langfuse 全链路 trace：**LLM 单点埋点**（所有 LLM 调用统一走 `llm_factory.call_llm`，每次调用一个 `llm.call` span），调用方只负责编排级 span（如 `ops_diagnose` 诊断链路），避免重复埋点；
- 未配置 Langfuse Key 时埋点全程 no-op：不构造客户端、不阻塞、不改变返回值与异常语义。

### 3.6 评估体系

| 评估项 | 脚本 / 数据 | 口径 |
|---|---|---|
| RAG 质量 | `scripts/run_eval.py` + RAGAS 4 指标（faithfulness / answer_relevancy / context_precision / context_recall） | 39 条数据集：`eval_qa.json`（25 条，运维手册，含 4 条对抗样本）+ `eval_mall_qa.json`（14 条，电商知识库，含 2 条对抗样本），对抗样本单独分组统计；常规样本预期四项 ≥ 0.7 |
| Agent 工具调用 | 单测覆盖（`tests/unit/test_tool_agent.py` 等）：task 意图触发工具、Retrieval Before Agency、参数缺失澄清、MCP 不可用降级、循环熔断 | 工具调用路径行为由测试保障 |
| 诊断根因命中率 | `scripts/run_eval_ops.py`：3 个预置故障场景（连接池耗尽 / 慢 SQL / 内存泄漏）强制 mock 数据源，报告 root_cause 与预期关键词比对 | 目标 3/3 命中 |

评估已知限制（面试可讲透）：`answer_relevancy` 对「诊断意图 → 枚举知识」型问答天然偏低（约 0.4-0.6），因反向生成的问题聚焦回答中的高密度实体而非原问题意图——属指标语义特性，不作为质量闸门；事实性看 faithfulness、检索质量看 context_precision / context_recall、诊断链路看根因命中率。

### 3.7 前端

Vue 3 + Element Plus + Pinia（JavaScript），原生 fetch 解析 SSE 流（`POST /api/v1/chat/ask`，事件：start / retrieving / rewriting / generating / tool_call / tool_result / done / error），意图分流过程可视化（改写提示、工具调用步骤、知识来源、诊断阶段标签）。

## 四、技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11 + FastAPI + LangChain 0.3 + LangGraph 0.2 |
| 前端 | Vue 3 + Element Plus + Pinia（JavaScript，非 TypeScript） |
| 向量库 | Qdrant 1.12 |
| 缓存 | Redis 7 |
| 元数据库 | PostgreSQL 15 |
| LLM | DeepSeek API（主）+ Ollama（备） |
| Embedding / Reranker | BAAI/bge-base-zh-v1.5（768 维）/ BAAI/bge-reranker-v2-m3 |
| MCP | FastMCP（Python 官方 SDK） |
| 可观测 | Langfuse |
| 运维数据 | Prometheus（演示 exporter 提供真实指标）/ 预置场景模拟（mock） |
| 评估 | RAGAS |
| CI/CD | GitHub Actions + husky pre-commit（ruff + pytest + vitest） |

## 五、快速启动（完整闭环）

### 1. 环境准备

```powershell
# 克隆仓库
git clone <repo-url>
cd AssistMind

# 后端虚拟环境
cd backend
python -m venv venv
venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY
```

### 2. 启动依赖服务（docker-compose，共 7 个服务）

```powershell
docker-compose up -d
```

| 服务 | 容器 | 端口 | 说明 |
|---|---|---|---|
| qdrant | smart-cs-qdrant | 6333 / 6334 | 向量库 |
| redis | smart-cs-redis | 6379 | 缓存 / 语义缓存 |
| postgres | smart-cs-postgres | 5432 | 元数据库（用户/工单/反馈） |
| langfuse-db / langfuse | smart-cs-langfuse-* | 3001 | 可观测平台（可选，开发环境默认启动） |
| ops-exporter | smart-cs-ops-exporter | 9100 | 演示业务指标 exporter（纯标准库，5 指标 × 5 服务） |
| prometheus | smart-cs-prometheus | 9090 | 真实 Prometheus（采集 exporter 指标） |

`OPS_DATA_SOURCE` 配置运维数据源模式：`auto`（默认，配置了 `PROMETHEUS_URL` 且健康探测通过则用真实数据，否则降级 mock）/ `mock` / `real`。未配置 `ALERTMANAGER_URL` / `ELASTICSEARCH_URL` 时，告警与日志证据返回空并标记 degraded。

`MALL_DATA_SOURCE` 配置电商业务数据源模式：`mock`（默认，内存演示清单，零依赖）/ `real`（PostgreSQL，先跑 init_db + seed_mall_db）/ `auto`（`SELECT 1` 健康探测通过则用 real，否则降级 mock）。real 模式下售后单（apply_refund）落库持久化，跨进程幂等（order_sn 唯一约束）。

### 3. 初始化数据库 + 灌知识库

```powershell
cd backend

# 建表 + 初始用户（admin/agent/user）
venv\Scripts\python.exe scripts/init_db.py

# 电商业务数据落 PostgreSQL（MALL_DATA_SOURCE=real 时使用；默认 mock 可跳过）
# 数据来源与 mock 演示清单同源（scripts/seed_mall_db.py 从 mock_source 导入，幂等）
venv\Scripts\python.exe scripts/seed_mall_db.py

# 电商知识库（knowledge/mall/：官方文档 + 自建业务规则，结构感知切块）
venv\Scripts\python.exe scripts/seed_mall_kb.py --reset

# 运维知识库（knowledge/ops/：4 篇排查手册）
venv\Scripts\python.exe scripts/seed_ops_kb.py --reset
```

### 4. 启动开发服务器

```powershell
# 后端（终端 1）
cd backend
uvicorn app.main:app --reload --port 8001

# 前端（终端 2）
cd frontend
npm install
npm run dev
```

也可直接运行根目录 `start-dev.ps1` 一键完成 Docker 依赖 + 初始化数据库 + 前后端启动（知识库灌库请按第 3 步单独执行）。

### 5. 访问

- 前端：http://localhost:5173
- 后端 API 文档：http://localhost:8001/docs
- Langfuse：http://localhost:3001
- Prometheus：http://localhost:9090
- 测试账号：admin/admin123（管理员）· agent/agent123（客服）· user/user123（普通用户）

## 六、知识来源与演示口径（诚实透明）

> 本节是项目的"诚信声明"，面试与演示前请先读。

1. **电商知识库素材基于开源项目**：`knowledge/mall/` 下官方内容（README、7 篇 reference 部署文档、3 个配置文件、`sql/mall_tables.sql` 表结构）全部取材自 [macrozheng/mall](https://github.com/macrozheng/mall)（Spring Boot 多模块电商商城开源项目），拉取于 2026-08-05，取材范围见 `knowledge/mall/SOURCES.md`。其中 `mall_tables.sql` 为节选：原始 `document/sql/mall.sql` 约 408KB，仅保留 **76 张 CREATE TABLE 表结构**（69KB），省略 DROP 与 INSERT 数据。
2. **业务规则与演示数据为项目自建**：`knowledge/mall/business/`（价格与优惠 / 会员与积分 / 发货与物流 / 售后服务政策 / 服务承诺与话术 / 演示商品与订单数据）为 AssistMind 自建内容，**字段锚定 mall.sql 官方表结构**（如 `oms_order_return_apply.status`、`pms_product.service_ids`、`ums_member_level`），数值口径属本项目演示配置。
3. **智能运维为受控演示环境**：3 个预置故障场景（连接池耗尽 / 慢 SQL / 内存泄漏）为受控演示数据；数据源为 mock/real 双实现，real 可对接 Prometheus / Alertmanager / Elasticsearch，`auto` 模式自动健康探测与降级。前端与诊断报告均有"模拟/真实数据"标注。
4. **不冒充真实交易系统**：本项目是智能客服系统（非商城系统），不处理真实交易、不承诺生产级售后/运维能力；订单、物流、售后数据均为演示清单（固定订单号 20240801001~20240801004 等），用于演示 Agent 工具调用闭环。

## 七、项目结构

```
AssistMind/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI 路由（chat/auth/knowledge/ticket/feedback/ops/health）
│   │   ├── agents/         # ToolAgent（LangGraph ReAct）+ OpsSupervisorAgent（三节点编排）
│   │   ├── core/
│   │   │   ├── rag/        # RAG 引擎（召回+RRF 融合+重排+CRAG）
│   │   │   ├── router/     # 意图路由（规则→语义→LLM，5 类意图）
│   │   │   ├── cache/      # L2 语义缓存（Redis 版本号失效）
│   │   │   ├── infra/      # Qdrant/Redis/PostgreSQL/LLM/Prometheus/ES/Alertmanager/Langfuse/断路器
│   │   │   ├── mcp/        # MCP Server（13 工具）+ Client
│   │   │   ├── mall/       # 电商业务数据源（门面 + mock/real 双实现 + 实体识别，字段锚定 mall.sql）
│   │   │   ├── ops/        # 运维数据源（门面 + mock/real 双实现 + 预置场景）
│   │   │   └── security/   # JWT + RBAC
│   │   ├── models/         # SQLAlchemy 模型（user/ticket/feedback/mall 业务表）
│   │   ├── schemas/        # Pydantic 模型
│   │   └── data/           # intent_routes.json / eval_qa.json / eval_mall_qa.json / ops_metric_exprs.json
│   ├── scripts/            # init_db / seed_mall_db / seed_mall_kb / seed_ops_kb / run_eval / run_eval_ops / ops_exporter
│   └── tests/              # 单元 + 集成测试
├── frontend/
│   └── src/
│       ├── views/          # 6 页面：Login/Chat/Ops/Tickets 可用，Knowledge/Admin 规划中
│       ├── stores/         # Pinia（auth/chat/knowledge/ticket/ops）
│       └── api/            # axios 封装（chat 为原生 fetch 解析 SSE）
├── knowledge/              # 知识库源文档（mall/ 官方取材 + 自建业务规则；ops/ 排查手册）
├── docs/                   # （空，文档规划中）
├── docker-compose.yml      # 7 服务
└── AGENTS.md               # AI 编码规则
```

## 八、评估命令

```powershell
cd backend

# RAGAS 评估（默认 eval_qa.json，也可传自定义数据集路径）
venv\Scripts\python.exe scripts/run_eval.py
venv\Scripts\python.exe scripts/run_eval.py app/data/eval_mall_qa.json

# 诊断根因命中率评估（3 个预置故障场景，强制 mock 数据源）
venv\Scripts\python.exe scripts/run_eval_ops.py
```

运行要求：`.env` 配置 `DEEPSEEK_API_KEY`；Qdrant 已启动并灌库（不可用时自动降级仅 BM25）。脚本以非 0 退出码区分失败原因（LLM 不可用 / 知识来源不可用 / 无有效得分），不假装成功。

## 九、面试要点

### 面试亮点速览

- **一条主叙事**：电商客服闭环（RAG 问答 + "我要退货" Agent 工具链路 + 工单沉淀），前端 SSE 全程可视化，可现场演示；
- **五层落地能力**：RAG 链路（混合检索/改写/CRAG/语义缓存/结构感知切块）→ Agent（ReAct + LangGraph）→ MCP 双向解耦 → 全链路降级 → Langfuse 可观测；
- **诚实的评估**：39 条带对抗样本的数据集 + 4 项 RAGAS 指标 + 诊断根因命中率 3/3，且讲得清指标语义特性；
- **透明口径**：知识来源（macrozheng/mall 开源取材 + 自建规则）与演示边界（受控运维环境）主动声明。

### 四类问题话术要点

1. **价值类（为什么做）**：客服场景高频问题可自动化（FAQ 咨询 / 订单物流查询 / 售后申请），但纯 RAG 只能"答"，Agent + 工具调用才能"办"（校验订单状态、创建售后单、沉淀工单）——答与办闭环是差异化点。
2. **五层落地类（怎么做）**：讲检索质量（混合召回 + RRF + Rerank + 结构感知切块解决长文档/SQL 召回）、讲可靠性（CRAG + 查询改写 + 语义缓存）、讲工程化（MCP 解耦 + 全链路降级 + Langfuse trace）、讲评估（RAGAS 4 指标 + 对抗样本 + 根因命中率）。
3. **迭代案例类（怎么演进）**：从纯 RAG 文档问答 → 增加 Agent 工具闭环（售后/工单）→ 扩展运维诊断（复用 RAG + Agent + MCP 底座）；意图路由从规则到语义到 LLM 的分层兜底是典型演进路径。
4. **验证指标类（怎么证明）**：常规样本 4 项 RAGAS ≥ 0.7、对抗样本单独统计不误导、诊断根因命中率 3/3、工具调用路径单测覆盖（含降级与熔断），并主动说明 answer_relevancy 在枚举型问答上的指标语义特性。

## License

MIT
