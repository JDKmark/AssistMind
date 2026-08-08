# phase8-dialog-slot-state Spec

- Mode: lite
- Status: draft
- Created: 2026-08-09

## Why

多轮对话状态管理只做了一半：history 传递（chat.py → ToolAgent/engine）、上下文三函数（core/dialog：trim/extract/format）、实体回溯（订单号/商品 ID 补填）已就位；但示例 DialogManager 的核心——**槽位状态机（update_state / is_complete）缺失**：

- 「退货需要 order_sn + reason」这类多槽位收集，当前完全靠 LLM 从 history 逐轮猜 + CLARIFY 追问，**没有显式的「还缺哪个槽位」确定性判断**（实体回溯只覆盖 order_sn/product_id，reason/title/description 无规则提取）
- run_eval_agent 退货流程能过（9/9），但依赖 LLM 恰好从历史读对；缺槽位时可能重复追问或编造

目标：新增轻量槽位状态机（纯函数，不存 session、不动前端/API 契约），为 ToolAgent 提供确定性槽位判断，减少 LLM 猜与重复追问。

## What Changes

- `backend/app/core/dialog/state.py`（新增）：DialogStateTracker 纯函数——槽位提取/意图→必需槽位映射/缺失判断
- `backend/app/core/dialog/__init__.py`：导出新函数
- `backend/app/agents/tool_agent.py`：think 实体提示注入时顺带注入缺失槽位提示（如「还缺退款原因」）
- `backend/tests/unit/test_dialog_state.py`（新增）：槽位提取/映射/缺失判断/集成
- 既有测试同步（如受影响）

## ADDED Requirements

### Requirement: 槽位确定性提取
系统 SHALL 从 query + history 中确定性提取对话槽位（规则优先，不依赖 LLM）：
- order_sn / product_id：复用 entity_extractor 规则（含 history 回溯）
- reason：退款原因（关键词「原因/因为/不想要/质量问题/七天无理由/重复购买/商品问题」等 + 用户已说出的原因内容）
- title / description：工单标题与描述（create_ticket 语境）

#### Scenario: 退货第二轮提取 reason
- GIVEN history 含「我要退货」「订单号是 20240801001」，当前 query「原因不想要了」
- WHEN 调用 extract_slots(query, history)
- THEN 返回 {order_sn: "20240801001", reason: "不想要了"}（order_sn 来自历史回溯，reason 来自当前轮）

#### Scenario: 缺 reason 判定
- GIVEN intent=refund，slots={order_sn: "20240801001"}
- WHEN 调用 missing_slots(intent, slots)
- THEN 返回 ["reason"]

### Requirement: 意图→必需槽位映射
系统 SHALL 定义各意图的必需槽位清单：
- refund（退货/退款）：order_sn + reason
- logistics（查物流）：order_sn
- order（查订单）：order_sn
- ticket（创建工单）：title + description

#### Scenario: 各意图映射
- WHEN 调用 required_slots("refund")
- THEN 返回 ["order_sn", "reason"]

### Requirement: ToolAgent 缺失槽位提示注入
系统 SHALL 在 ToolAgent think 已抽取实体时，顺带注入缺失槽位提示（如「（系统提示：已提取订单号，还缺退款原因）」），帮助 LLM 精确追问，不重复索要已有槽位。

#### Scenario: 退货缺 reason 注入提示
- GIVEN 用户已提供 order_sn 未提供 reason，ToolAgent think 被调用
- THEN messages 中注入含「还缺退款原因」的提示，且不含「索要订单号」

## MODIFIED Requirements

（无）

## REMOVED Requirements

（无）

## RENAMED Requirements

（无）

## Tasks

- Task 1: dialog/state.py 槽位状态机（extract_slots / required_slots / missing_slots）→ Req: 槽位确定性提取 / 意图→必需槽位映射
  - 1.1 实现 extract_slots：复用 entity_extractor（order_sn/product_id）+ 新增 reason/title/description 规则
  - 1.2 required_slots / missing_slots 映射
  - 1.3 test_dialog_state.py（TDD：先写失败测试）
    Files: backend/app/core/dialog/state.py, backend/app/core/dialog/__init__.py, backend/tests/unit/test_dialog_state.py
    Run: cd backend; venv\Scripts\python.exe -m pytest tests/unit/test_dialog_state.py -q
    Expected: 全部通过
- Task 2: ToolAgent 集成（think 注入缺失槽位提示）→ Req: ToolAgent 缺失槽位提示注入
  - 2.1 think 实体提示分支：从 state 取 query/history，extract_slots + missing_slots，注入「还缺 xxx」提示
  - 2.2 更新/新增 test_tool_agent.py 用例（退货缺 reason 注入）
    Files: backend/app/agents/tool_agent.py, backend/tests/unit/test_tool_agent.py
    Run: cd backend; venv\Scripts\python.exe -m pytest tests/unit/test_tool_agent.py -q
    Expected: 全部通过
- Task 3: 全量回归 + run_eval_agent 验证 → Req: 全部
  - 3.1 pytest -k "not integration" 全绿
  - 3.2 run_eval_agent.py --entity-fill both 退货流程保持通过（9/9 不回归；理想：多轮任务少一轮追问）
    Run: cd backend; venv\Scripts\python.exe -m pytest tests -q -k "not integration"
    Expected: 全绿；run_eval_agent 退货任务通过

# Task Dependencies
- Task 2 依赖 Task 1（先有槽位状态机再集成）
- Task 3 依赖 Task 1、2

## 验收清单
- [ ] Scenario: 退货第二轮提取 reason → extract_slots 返回 {order_sn, reason}
- [ ] Scenario: 缺 reason 判定 → missing_slots 返回 ["reason"]
- [ ] Scenario: 各意图映射 → required_slots 映射正确
- [ ] Scenario: 退货缺 reason 注入提示 → think 注入「还缺退款原因」且不重复索要订单号
- [ ] test_dialog_state.py / test_tool_agent.py 全绿
- [ ] 全量单测无回归（332 → 新增后）
- [ ] run_eval_agent 退货流程 9/9 保持（理想：多轮任务追问轮次减少）
