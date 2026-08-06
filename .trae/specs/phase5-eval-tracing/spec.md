# phase5-eval-tracing Spec

## Why

- README 承诺的 RAGAS 评估（4 指标 ≥0.7）与 Langfuse 全链路追踪目前**只有声明没有实现**：`scripts/run_eval.py` 不存在、评估数据集不存在、app 代码零 langfuse import（`main.py:83` 还是 `# TODO: Phase 4：Langfuse`）。
- ops 诊断链路（Supervisor Agent 的 plan→collect→analyze + SSE 事件 + 自动建单）完全不可观测，无法回答"诊断为什么慢/哪一步失败/报告质量如何"。
- 目标是补齐两件事：**全链路可观测**（Langfuse 埋点，含 LLM 全局与 ops 链路）与**可量化评估**（RAG 问答 RAGAS 4 指标 + 诊断根因命中率），并让 README 的承诺变成真实可运行的东西。

## What Changes

新增：
- `backend/app/core/infra/langfuse.py`：Langfuse 客户端单例（照 infra 模式，未配置 key 时禁用，不阻塞启动）
- `backend/scripts/run_eval.py`：RAG 问答 RAGAS 评估脚本（对齐 README 承诺）
- `backend/scripts/run_eval_ops.py`：ops 诊断根因命中率评估脚本（3 个预置场景）
- `backend/app/data/eval_qa.json`：RAG 评估数据集（≥10 条，含对抗样本）

修改：
- `backend/app/core/infra/llm_factory.py`：`call_llm` 内嵌 Langfuse span（provider/model/token/耗时），一次埋点覆盖全部 7 个 LLM 消费者
- `backend/app/agents/ops_supervisor.py`：诊断 trace（query→plan→evidence 摘要→report）与节点 span
- `backend/app/api/ops.py`：SSE 阶段与自动建单事件埋点
- `backend/app/main.py`：lifespan 初始化 Langfuse（未配置跳过）
- `backend/app/api/health.py`：依赖状态加 langfuse（ok/disabled）
- `backend/tests/unit/`：新增埋点与数据集相关单测
- `README.md` / `AGENTS.md`：评估与埋点章节对齐

## Impact

- 兼容约束：接口返回形状零变更（前端 /api/v1/ops/* 与 SSE 事件不受影响）；`call_llm` 签名不变（7 个消费者无需改动）。
- 配置：复用现有 `LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY`；未配置时所有埋点自动 no-op，服务行为与现在完全一致。
- 依赖：langfuse 4.14.2、ragas 0.4.3 已在 venv，无需新增依赖。
- 风险：langfuse 4.x 的装饰器/手动 API 行为差异 → 实现时以官方文档为准，锁定行为验收（见 Requirement 1/2）。
- 评估脚本需要真实 LLM（DeepSeek）与已灌库的 Qdrant（RAG 链路）或 mock 数据源（ops 链路），属开发期工具，不进 CI 单元测试。

## ADDED Requirements

### Requirement: Langfuse 生命周期与开关
系统 SHALL 在 lifespan 启动时初始化 Langfuse 客户端；未配置 LANGFUSE_PUBLIC_KEY/SECRET_KEY 时系统 SHALL 正常启动、正常提供服务，且所有埋点路径不产生异常、不产生外部请求。

#### Scenario: 未配置 key 启动
- WHEN 环境未配置 LANGFUSE_PUBLIC_KEY 且应用启动
- THEN 服务正常可用，health 接口返回 langfuse 状态为 disabled，诊断与问答功能与未接入前行为一致

#### Scenario: 配置 key 启动
- WHEN 配置了 LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY
- THEN lifespan 初始化成功，health 接口返回 langfuse 状态为 ok

### Requirement: LLM 全局埋点
系统 SHALL 在 `call_llm` 每次调用中产生一个 Langfuse span，元数据含 provider（deepseek/ollama）、model、超时与重试次数；span 输出含生成内容长度与耗时。

#### Scenario: 聊天/RAG/诊断调用均被追踪
- WHEN 任意消费者（chat、query_rewriter、critic、intent、ops plan/analyze、ToolAgent）调用 `call_llm`
- THEN 产生一次带 provider/model 元数据的 span，且不改变调用返回值与异常行为

### Requirement: ops 诊断链路追踪
系统 SHALL 为每次诊断（POST /api/v1/ops/diagnose）建立一条 trace：根含 query，子 span 覆盖 supervisor 决策（plan）、证据采集（collect，含各数据源条目数与 degraded 列表）、综合分析（analyze）；SSE 各阶段事件与自动创建工单（ticket_id）记录在 trace 输出中。

#### Scenario: 完整诊断追踪
- WHEN 用户触发一次诊断且 Langfuse 已启用
- THEN trace 包含 plan/collect/analyze 三个 span 与 report 摘要、degraded、ticket_id（如有）

#### Scenario: 诊断失败追踪
- WHEN 诊断流抛出异常
- THEN trace 记录错误信息，SSE 仍按现有行为返回 error 事件

### Requirement: RAGAS 评估脚本与数据集
系统 SHALL 提供 `scripts/run_eval.py`，基于 `backend/app/data/eval_qa.json` 数据集（≥10 条，含对抗样本）运行 RAG 链路，输出 faithfulness / answer_relevancy / context_precision / context_recall 四项指标与逐条明细。

#### Scenario: 运行评估
- WHEN 执行 `venv\Scripts\python.exe scripts/run_eval.py`（Qdrant 已灌库、LLM 可用）
- THEN 输出每条样本的 context/answer 与四项指标分数，末尾输出平均分

#### Scenario: 数据集校验
- WHEN 评估脚本加载数据集
- THEN 校验每条含 question/ground_truth 且非空，非法条目跳过并告警

### Requirement: 诊断根因命中率评估
系统 SHALL 提供 `scripts/run_eval_ops.py`：对 3 个预置故障场景（mock 数据源）各触发一次诊断，将报告 root_cause 与场景预期根因（scenarios.py 的 root_cause）做关键词命中比对，输出每场景命中/未命中与总体命中率。

#### Scenario: 运行诊断评估
- WHEN 执行 `venv\Scripts\python.exe scripts/run_eval_ops.py`
- THEN 输出 3 场景各自的命中判定与总体命中率（命中 = root_cause 包含预期根因关键词之一）

### Requirement: 埋点与数据集单元测试
系统 SHALL 为新增能力补充单元测试（mock 外部依赖，`-k "not integration"` 可跑）。

#### Scenario: 测试覆盖
- WHEN 运行 `venv\Scripts\python.exe -m pytest tests -q -k "not integration"`
- THEN 覆盖：Langfuse 未配置时 no-op、call_llm 埋点调用断言（mock）、ops trace 结构断言（mock）、eval_qa.json 格式校验

## MODIFIED Requirements

（无）

## REMOVED Requirements

（无）
