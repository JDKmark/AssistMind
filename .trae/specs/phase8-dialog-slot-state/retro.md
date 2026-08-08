# phase8-dialog-slot-state Retro

## 结论
目标达成：槽位状态机（extract_slots / required_slots / missing_slots）落地，
ToolAgent think 注入缺失槽位提示；测试 +11（test_dialog_state 10 + tool_agent 1），
全量 343 passed 无回归；退货核心场景冒烟验证通过（{order_sn, reason} 无缺失）。

## 经验
1. **纯函数无状态方案成本极低**：不动前端/API 契约、不引 session/Redis，规则层
   确定性优先（与 entity_extractor 一脉相承），既有 11 个 tool_agent 用例字节级
   不变——「轻量槽位」在无 session 场景下够用，完整 session 状态机（Redis）留作
   后续跨进程需求再上。
2. **TDD 边界清晰**：先写测试确认 ImportError（模块不存在）→ 最小实现 → 通过；
   集成用例先断言「还缺reason not in prompt」确认失败再实现——失败原因与功能
   缺失严格对应。
3. **意图粗判关键词**：_guess_intent 按退款>物流>订单>工单优先级，无法判断时
   跳过注入（行为与原来完全一致）——保守降级，不误伤其他意图。

## 下次避免
- 子代理报告的 ruff 既有错误（E501 长中文行等）是项目存量，与本次无关——
  已用 git show HEAD 比对确认，未误动。
