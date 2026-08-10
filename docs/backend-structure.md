# AssistMind 后端结构设计

> 由 code-structuring 技能产出（模式判定 → 模块清单 → 层间契约 → 现有代码映射 → 落地建议）。
> 最后更新：2026-08-08。结构变化时更新本文档，作为 spec 的 Why/What Changes 前置输入。

## Why（现状缺口）

AssistMind 后端是「分层式骨架 + Agent 层内图编排」的混合架构，整体分层清晰
（api → core → infra），但按六模块客服 Agent 参考结构（preprocessor /
intent_classifier / entity_extractor / dialog_manager / knowledge_base /
response_generator）逐项对账后发现一个明确缺口：

- **dialog_manager 职责散落三处**：`api/chat.py:75-81`（history 记忆窗口裁剪）、
  `agents/base.py:115`（_extract_query 从消息列表提取当前问题）、
  `rag/engine.py:222-227`（history 拼接为「用户/客服」文本）——同一职责
  （对话上下文管理）分散在三个模块，无统一入口、无专项测试。
- 其余五模块均有对应实现（见映射表），结构健康。

目标：集中 dialog_manager 职责为独立模块（core/dialog/），其余结构保持，
产出可复用的结构设计文档。

## 模式判定

- **采用：分层式（layered）+ Agent 层内图编排式（graph）混合**
- 理由：依赖单向（api → core → infra）符合分层式；Agent 循环（ReAct /
  Orchestrator-Workers）是图编排式。新增 core/dialog 属于分层式内的一层新模块。
- 参考：六模块客服 Agent（职责划分参考；控制流不参考——本项目是图编排式）

## 模块清单

| 模块 | 职责（一句话） | 接口签名 | 依赖 |
|---|---|---|---|
| `api/chat` | SSE 聊天入口：意图分流 + 事件流编排 | `POST /api/v1/chat/ask` → SSE | agents、core/router、core/rag、core/dialog |
| `agents/tool_agent` | ReAct 循环 + MCP 工具调用 + 实体补填 | `run(query, history) -> {answer, tool_calls, ...}` | core/mcp、core/mall/entity_extractor、core/dialog |
| `agents/ops_supervisor` | 运维诊断三节点编排（LangGraph 壳，流程见 core/ops/pipeline） | `run(query) -> {report, ...}` | core/ops/pipeline、core/rag |
| `core/ops/pipeline` | 诊断流水线：计划 → 采集 → 分析（Agent 与 SSE 流式共用） | `_plan(query)` / `collect(plan, query)` / `analyze(query, evidence)` | core/ops/data_source、core/ticket_service、core/rag |
| `core/router/intent` | 三级意图路由（规则→语义→LLM） | `route(query) -> {intent, confidence, source}` | core/data（intent_routes.json） |
| `core/mall/entity_extractor` | 订单号/商品 ID 规则抽取 + 工具参数补填 | `extract(query, history) -> {order_sn, product_id}` | 无 |
| `core/rag/engine` | RAG 检索 + 生成编排 | `retrieve(query) -> {contexts, crag}` / `generate(...)` | core/rag/*、core/dialog |
| `core/mcp/server` | MCP 工具注册（13 个） | `@mcp.tool()` 声明 | core/mall、core/ops、core/rag |
| `core/dialog` | 对话上下文管理：裁剪/提取/格式化 + 槽位状态机 | `trim_history(history)` / `extract_query(messages)` / `format_history(history)` / `extract_slots(query, history)` / `missing_slots(intent, slots)` | 无 |

## 层间数据契约

```
HTTP POST /api/v1/chat/ask {query, history?}
  → [api/chat] 意图分流
      → faq:  [core/rag] retrieve(query, history) → generate(query, contexts, history)
      → task: [agents/tool_agent] run(query, history) → {answer, tool_calls}
      → chat: LLM 直连（历史拼 prompt）
      → diagnose: [agents/ops_supervisor] run(query) → {report}
  → SSE 事件流（start/tool_call/tool_result/done/...）

[core/dialog]（新增，统一对话上下文）
  trim_history(history: list[{role, content}] | None) -> list | None
      # MEMORY_WINDOW 裁剪，语义与 engine.generate 一致
  extract_query(messages: list[Message]) -> str
      # 取最后一条 HumanMessage 的 content（多轮依赖上文）
  format_history(history: list[{role, content}]) -> str
      # "用户: xxx\n客服: xxx" 拼接（engine 历史 prompt 格式）
```

## 依赖方向与数据流图（ASCII）

```
[api/chat] ─────────────────────────────────────────────┐
    │ 意图分流                                          │
    ├──> [agents/tool_agent] ──> [core/mcp/client] ──> [core/mcp/server]
    │        │                      (HTTP streamable)
    │        └──> [core/mall/entity_extractor]
    ├──> [core/rag/engine] ──> [core/rag/*]（chunking/bm25/qdrant/reranker/critic）
    ├──> [agents/ops_supervisor] ──> [core/ops/*]
    └──> [core/dialog]（全部意图共用：裁剪 history / 提取 query / 格式化历史 / 槽位状态机）
                ▲
                └── 消费方：api/chat、agents/base、agents/tool_agent、core/rag/engine
```

## 与现有代码映射

| 目标模块 | 现有代码（file:line） | 动作 |
|---|---|---|
| core/dialog（trim_history） | `api/chat.py:75-81`（history[-MEMORY_WINDOW:]） | 抽取 |
| core/dialog（extract_query） | `agents/base.py:115-126`（_extract_query） | 抽取 |
| core/dialog（format_history） | `rag/engine.py:220-225`（history_text 拼接） | 抽取 |
| core/router/intent | `core/router/intent.py:97`（route 三级） | 保持（函数式已健康） |
| core/mall/entity_extractor | `core/mall/entity_extractor.py` | 保持 |
| core/rag/engine | `core/rag/engine.py:88/196`（retrieve/generate） | 保持 |
| core/mcp/server | `core/mcp/server.py` | 保持 |
| 六模块中其余 | — | 已覆盖 |

## 落地建议（增量路径）

1. **第 1 步（本次）**：新建 `core/dialog/manager.py`，抽取三处职责为
   `trim_history` / `extract_query` / `format_history`（纯函数，行为不变）
2. **第 2 步（本次）**：消费方改调用（chat.py / base.py / engine.py），
   新增 `tests/unit/test_dialog_manager.py`
3. **验收**：pytest 全绿（行为零变更）；职责无重叠；依赖单向；
   符合 AGENTS.md「核心逻辑 → app/core/ 功能名.py 或子包」放置规则

## 验收核对

- [x] 每个模块职责一句话且无重叠（dialog 职责集中后）
- [x] 依赖方向单向、无环
- [x] 接口签名含输入/输出形状（可测）
- [x] 与 AGENTS.md 放置规则一致（core/dialog/ 子包）
- [x] 数据契约覆盖主流程每一跳（SSE 事件 / 意图分流 / dialog 三方法）
