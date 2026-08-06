# phase5-eval-tracing Tasks

- [ ] Task 1: Langfuse 基础设施
  - [ ] 1.1 新增 `backend/app/core/infra/langfuse.py`：`get_langfuse()` 单例 + `is_langfuse_enabled()`；未配置 key 返回 None，不抛异常
  - [ ] 1.2 `backend/app/main.py` lifespan 初始化（未配置跳过，打 info 日志）
  - [ ] 1.3 `backend/app/api/health.py` 依赖状态加 langfuse（ok / disabled）
  - [ ] 1.4 参考 langfuse 4.14 官方文档确认异步场景推荐用法（装饰器 vs 手动 trace/span），把结论写进代码注释

- [ ] Task 2: LLM 全局埋点（依赖 Task 1）
  - [ ] 2.1 `backend/app/core/infra/llm_factory.py` 的 `call_llm` 内嵌 span：provider/model/耗时/输出长度，重试与降级路径也记录
  - [ ] 2.2 埋点不改变返回值与异常语义（7 个消费者零改动）

- [ ] Task 3: ops 诊断链路埋点（依赖 Task 1）
  - [ ] 3.1 `backend/app/agents/ops_supervisor.py`：`run()` 建 trace（name=ops_diagnose，input=query）；plan/collect/analyze 节点各一个 span，collect 输出含各数据源条目数与 degraded，analyze 输出 report 摘要
  - [ ] 3.2 `backend/app/api/ops.py` 的 `_diagnose_stream`：SSE 阶段事件与 ticket_id 记录进 trace；异常记录错误
  - [ ] 3.3 未启用 Langfuse 时全部 no-op（行为不变）

- [ ] Task 4: RAGAS 评估脚本与数据集（独立）
  - [ ] 4.1 新增 `backend/app/data/eval_qa.json`：≥10 条 QA（question/ground_truth），其中 ≥3 条对抗样本（跨知识域/无对应文档/易混淆）
  - [ ] 4.2 新增 `backend/scripts/run_eval.py`：加载数据集 → 跑 RAG 检索+生成（复用 `app.core.rag.engine`）→ ragas 4 指标 → 逐条明细 + 平均分；数据集非法条目跳过并告警
  - [ ] 4.3 README「评估」章节与脚本真实行为对齐（命令、输出、预期）

- [ ] Task 5: 诊断根因命中率评估（独立）
  - [ ] 5.1 新增 `backend/scripts/run_eval_ops.py`：mock 数据源 + 3 个预置场景（conn_pool_exhausted / slow_sql / memory_leak）各触发一次诊断（真实 LLM），比对 report.root_cause 与场景预期根因关键词，输出逐场景判定与总体命中率

- [ ] Task 6: 单元测试与文档（依赖 Task 1-5）
  - [ ] 6.1 `tests/unit/test_langfuse_infra.py`：未配置 key 时 get_langfuse() 为 None / 禁用状态；配置时初始化成功（mock client）
  - [ ] 6.2 `tests/unit/test_llm_langfuse.py`：mock langfuse 后断言 call_llm 产生 span 调用（成功与失败路径）
  - [ ] 6.3 `tests/unit/test_ops_langfuse.py`：mock langfuse 后断言 ops trace 结构（run/节点/SSE 阶段），未启用时无调用
  - [ ] 6.4 `tests/unit/test_eval_data.py`：eval_qa.json 格式校验（字段完整、条数、对抗样本标注）
  - [ ] 6.5 `AGENTS.md` 补 Langfuse 埋点约定与评估脚本说明

# Task Dependencies
- Task 2 依赖 Task 1（需要 langfuse 单例与初始化）
- Task 3 依赖 Task 1（同上）
- Task 4 独立，可与 Task 1 并行（不依赖埋点）
- Task 5 独立，可与 Task 1、4 并行（不依赖埋点）
- Task 6 依赖 Task 1、2、3、4（测试覆盖上述实现）
