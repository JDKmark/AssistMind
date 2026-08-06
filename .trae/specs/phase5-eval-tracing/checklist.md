# phase5-eval-tracing Checklist

- [ ] 未配置 LANGFUSE key 时服务正常启动、健康检查通过、诊断与问答行为与接入前一致（health 返回 langfuse=disabled）
- [ ] 配置 key 后 `call_llm` 每次调用产生带 provider/model 元数据的 span，返回值与异常行为不变
- [ ] 一次诊断产生完整 trace：plan/collect/analyze 三个 span + SSE 阶段事件 + report 摘要 + degraded + ticket_id（如有）
- [ ] 诊断异常时 trace 记录错误，SSE 仍按现有行为返回 error 事件
- [ ] `scripts/run_eval.py` 存在且可运行，输出 faithfulness/answer_relevancy/context_precision/context_recall 四项与逐条明细
- [ ] `backend/app/data/eval_qa.json` ≥10 条且含 ≥3 条对抗样本，字段完整（question/ground_truth）
- [ ] `scripts/run_eval_ops.py` 输出 3 场景（conn_pool_exhausted/slow_sql/memory_leak）各自命中判定与总体命中率
- [ ] 新增单测（langfuse no-op、call_llm 埋点、ops trace 结构、eval 数据格式）全部通过，`pytest -k "not integration"` 全绿
- [ ] 既有测试无回归（后端 pytest 非集成 + 前端 npm run test:unit）
- [ ] 无前端改动、无 TypeScript 文件、无新增 UI 库
- [ ] 无新增依赖（langfuse/ragas 已存在于 pyproject）
- [ ] README 评估章节与实际脚本一致；AGENTS.md 补充埋点与评估约定
