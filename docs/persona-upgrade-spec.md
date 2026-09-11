# 客服人格体系升级 Spec（persona-upgrade）

> 目标读者：编码 Agent。按本 Spec 逐项实现，不要扩大范围。
> 项目：AssistMind（电商智能客服，FastAPI + LangGraph + MCP，后端 `backend/`，Python 3.11）
> 前置约束：先读 `AGENTS.md`，其中所有架构约束（异步优先、失败降级不静默、L1 缓存 hash 不可 WRONGTYPE、ReAct 文本协议不宜流式等）对本任务全部适用。

## 1. 背景与目标

当前人格库（`backend/app/data/personas.json` + `backend/app/core/personas.py`）存在 4 个已核实缺陷：

| # | 缺陷 | 证据 |
|---|------|------|
| D1 | task 意图不经过人格，同一会话内语气割裂 | `backend/app/api/chat.py` 中 `persona_suffix` 只传给 `_handle_faq` / `_handle_chat`，`_handle_task`（约 L422-472）未传 |
| D2 | 选人格即放弃语义缓存（L1/L2 均跳过命中与写入），人格用户每轮付全量成本 | `chat.py` 中 `if persona_suffix is None:` 两道守卫（命中约 L307-310，写入约 L400-401） |
| D3 | 人格定义仅 1-2 句语气指令，无结构化维度、无 few-shot，LLM 遵循度不稳定 | `personas.json` 全文 |
| D4 | 静态人格，投诉/愤怒场景下「活泼客服」仍俏皮，无情绪安全阀 | 无任何场景判断逻辑 |

目标：在**不改变 RAG 事实性约束与客服职责**的前提下，完成 P0（必修）与 P1（必修）改造；P2 为可选项，时间允许再做。

## 2. 范围总览

| 项 | 内容 | 优先级 | 涉及文件 |
|---|------|--------|----------|
| T1 | task 意图接入人格（仅最终用户可见文本） | P0 | `app/api/chat.py`、`app/agents/tool_agent.py` |
| T2 | 语义缓存按 persona 分桶（替代"选人格跳过缓存"） | P0 | `app/core/cache/semantic_cache.py`、`app/api/chat.py` |
| T3 | personas.json 升级为结构化 voice card + 组装逻辑 | P1 | `app/data/personas.json`、`app/core/personas.py` |
| T4 | 情绪安全阀（规则层，人格让位） | P1 | `app/core/personas.py`、`app/api/chat.py` |
| T5 | 评估补充人格一致性样本与打分 | P2 | `app/data/eval_*.json`、`scripts/run_eval.py`（仅追加，不改既有指标口径） |
| T6 | unclear 兜底文案过人格 | P2 | `app/api/chat.py` |

非目标（Out of Scope，明确不做）：
- 不引入情绪识别模型/第三方情感分析服务，T4 只做规则层。
- 不改 ReAct 决策协议（Action/Action Input 文本格式），不在 task 链路做流式。
- 不改前端人格选择器 UI（`frontend/src/stores` persona store 与请求体 `persona` 字段保持不变）。
- 不改 RAGAS 4 指标与既有评估口径、不改 `intent_routes.json`。
- 不做租户级人格配置（B 端配置项形态），保留 C 端选择器演示形态。

## 3. T1 — task 意图接入人格（P0）

**现状**：`chat.py` L235 计算 `persona_suffix = persona_prompt(req.persona)`；`_handle_task(query, history, access_token, conversation_id, user_id)` 签名无人格参数。`ToolAgent.__init__` 已支持 `system_prompt: str | None` 参数（`tool_agent.py` L106-112），默认 `DEFAULT_SYSTEM_PROMPT`（L28）。

**改动**：

1. `_handle_task` 增加关键字参数 `persona_suffix: str | None = None`；`chat.py` 分流处（L248-256）把 `persona_suffix` 传入。
2. 构造 ToolAgent 时：若 `persona_suffix` 非空，用 `ToolAgent(system_prompt=f"{DEFAULT_SYSTEM_PROMPT}\n{persona_suffix}\n{TASK_PERSONA_GUARD}", ...)`，否则保持默认构造（`system_prompt` 不传，行为与现状完全一致）。

   `TASK_PERSONA_GUARD` 为固定文案，定义在 `tool_agent.py`：
   ```
   注意：以上语气要求仅适用于 Final Answer 与 CLARIFY 的用户可见文本；
   Thought/Action/Action Input 的输出格式与工具决策逻辑不受语气要求影响，保持原有协议。
   ```
3. **禁止**把人格注入 search_knowledge 的 query 改写或工具参数；人格只影响面向用户的自然语言。

**风险点**：人格文本可能诱导 LLM 在 Action 行前加语气词导致 `parse_action` 解析失败。验收测试必须覆盖「带人格跑完整 ReAct 循环」。

## 4. T2 — 语义缓存按 persona 分桶（P0）

**现状**：`semantic_cache.py` 键结构为 `assistmind:cache:l1:{role}:` 与 `assistmind:cache:l2:data:{role}`（L39-41），`get(query, role)` / `set(query, answer, sources, role)` 按 role 分桶。`chat.py` 在 `persona_suffix is None` 时才查/写缓存。

**改动**：

1. `semantic_cache.get` / `set` 增加关键字参数 `persona: str = ""`，桶维度从 `{role}` 变为 `{role}:{persona or "default"}`：
   - 在模块内新增私有函数 `_bucket(role: str, persona: str) -> str`，返回 `f"{role}:{persona or 'default'}"`；
   - `_l1_key_for` / `_l2_data_key` / `_l2_lookup` / `_l2_delete` 内部统一改用 `_bucket`，**对外签名只加 `persona` 参数，调用方不传时行为与现状逐字节一致**（`user:default`）。
2. `chat.py` `_handle_faq`：删除 `persona_suffix is None` 守卫，缓存查/写统一改为 `get(query, role=role, persona=req_persona_or_empty)` / `set(..., persona=...)`。注意 `_handle_faq` 当前只收到 `persona_suffix`（组装后文本），需要把原始 persona id 一并传入——给 `_handle_faq` 增加 `persona_id: str = ""` 参数，由分流处传 `req.persona or ""`。
3. AGENTS.md 既有红线继续有效：L1 用 hash 内 `_expires_at` 字段控 TTL，禁止 `redis.set` 同 key 写 string；版本号 INCR 失效机制不动；`purge()` 的 SCAN 模式（`assistmind:cache:l1:*`、`assistmind:cache:l2:data:*`）天然覆盖新桶，无需改。
4. 旧格式缓存条目（`{role}` 无 persona 段）升级后自然 miss，属一次性冷启动，可接受；不做数据迁移。

## 5. T3 — personas.json 结构化 voice card（P1）

**现状**：每人格仅 `name / description / system_prompt` 三字段，`persona_prompt()` 直接返回 `system_prompt` 字符串。

**新 schema**（`personas.json`，三个人格全部改写，向后兼容旧字段）：

```json
{
  "lively": {
    "name": "活泼客服",
    "description": "热情俏皮，互动感强",
    "tone": {"formal": 2, "playful": 5, "empathy": 3},
    "rules": {
      "do": ["可用「好嘞」「马上帮您看」等轻松表达", "每条回答最多一处语气词或表情符号"],
      "dont": ["不把不确定的信息说得绝对", "业务规则、金额与时效表述必须严谨"]
    },
    "few_shots": [
      {"q": "运费谁出？", "a": "好嘞，帮您看了下：本店商品支持包邮，退货时运费险覆盖首重，您这边不用承担运费～"}
    ]
  }
}
```

- `tone` 为 1-5 定位标尺（formal 越高校正式、playful 越高越俏皮、empathy 越高越强调共情），三个人格取值需拉开区分度；`professional / gentle / lively` 语义保持与现状一致。
- 每人格 `few_shots` 至少 2 条，示例问题从真实客服场景选（运费/退货/物流/优惠），答案必须符合该人格语气且事实正确（对齐 `knowledge/mall/business/` 政策文档）。

**`personas.py` 改动**：

1. 新增 `_assemble_prompt(cfg: dict) -> str`：把 tone 标尺翻译为一句定位描述 + do/don't 清单 + few-shot 示例，组装成完整语气指令段。few-shot 以「示例：用户问「…」→ 回答「…」」形式拼接。
2. `persona_prompt()` 改为：读到新 schema 走 `_assemble_prompt`；读到老 schema（仅有 `system_prompt` 字段）直接返回该字段——**向后兼容**，便于回滚。
3. `list_personas()` 不变（仍不暴露 prompt 内容，few_shots 也不暴露）。
4. mtime 热加载、解析失败沿用旧缓存 + `logger.warning` 的机制原样保留。

## 6. T4 — 情绪安全阀（P1）

**设计**：规则层检测，命中时在 persona 指令**之前**插入共情优先指令，人格让位。

1. `personas.py` 新增：
   ```python
   _NEGATIVE_KEYWORDS = ["投诉", "气死", "垃圾", "太差", "再也不", "曝光", "赔偿", "欺骗"]

   def emotion_override(query: str) -> str | None:
       """规则层情绪检测：命中返回共情优先指令，否则 None。"""
   ```
   触发条件（任一）：命中 `_NEGATIVE_KEYWORDS`；或查询中 `！`/`!` 连续出现 ≥2 次。
   返回文案：`用户当前情绪明显负面，本轮回答优先共情安抚（先理解与道歉，再给解决方案），收敛俏皮与营销化表达；所有人格语气要求在本轮让位于该原则。`
2. `chat.py` 分流前计算 `override = emotion_override(req.query)`；最终注入顺序为：既有 system → `override`（若非 None）→ `persona_suffix`。faq / chat / task 三条链路统一应用（复用 T1 的参数通道）。
3. 命中时 `logger.info("[Personas] 情绪安全阀触发，人格让位")`，并在 Langfuse span metadata 记 `emotion_override=True`（仅当 langfuse 已启用，保持未启用时 no-op）。

## 7. T5 — 评估补充（P2，可选）

- 新增 `app/data/eval_persona.json`：≥6 条样本，每条含 `question / persona / expected_tone_keywords / forbidden_keywords`（如 lively 的 forbidden 含「 sorry 式机械道歉堆叠」，professional 的 forbidden 含语气词「啦/哦/嘞」）。
- `scripts/run_eval.py` 不改既有流程；新增独立脚本 `scripts/run_eval_persona.py`：对每条样本带 persona 调 `/api/v1/chat/ask`（或直接调 engine），规则校验 expected/forbidden 关键词命中率并输出报告。失败样本 `logger.warning` 列出，退出码非 0。

## 8. T6 — unclear 兜底过人格（P2，可选）

`_CLARIFY_ANSWER` 目前为固定文案直出。改动：unclear 分支也把 `override + persona_suffix` 交给一次轻量 `stream_llm` 改写（fast 模式，`call_llm` 快速失败档），LLM 不可用时回落原文案。**注意失败预算**：该改写属「失败可降级」环节，必须用 fast 链路，不得增加 unclear 分支的感知延迟超过 16s。

## 9. 测试要求（全部新增，不改既有用例语义）

后端单测（`backend/tests/unit/`）：

1. `test_personas_assemble.py`：新 schema 组装结果含 tone 描述/do/don't/few-shot；老 schema 直通；未知 persona 返回 None 且 warning。
2. `test_persona_task.py`：带 persona 的 task 调用，mock LLM 走完整 ReAct 循环，断言 `parse_action` 正常解析（人格不破坏协议）、Final Answer 中含人格语气特征、system_prompt 含 TASK_PERSONA_GUARD。
3. `test_semantic_cache_persona.py`：同 query 同 role 不同 persona 互不命中；不传 persona 与旧行为一致（`user:default` 桶）。
4. `test_emotion_override.py`：关键词命中/感叹号密度命中/正常查询不命中；override 在注入顺序中位于 persona 之前。
5. 回归：既有 `test_tool_agent.py`、缓存相关测试全部保持通过。

运行：`cd backend; venv\Scripts\python.exe -m pytest tests -q -k "not integration"`，新增用例全绿且既有用例零失败。

## 10. 验收标准

1. 同一会话内，选定人格后 faq / chat / task 三类意图的回答均体现该人格语气，工具调用过程与结果数据不变。
2. 带人格的 faq 请求第二次命中语义缓存（日志出现 L1/L2 命中），不同人格之间不串缓存。
3. personas.json 热加载：改 few-shot 后下次请求生效，不重启服务。
4. 输入含投诉关键词时，即使选「活泼客服」，回答也为共情优先风格。
5. 全量单测通过；`AGENTS.md` 中与人格/缓存相关的既有约束（热加载、不静默失败、L1 hash 结构）未被破坏。

## 11. 文档同步

实现完成后更新：
- `AGENTS.md` 人格相关条目：补充「缓存按 role+persona 分桶」「情绪安全阀规则层」「task 链路人格仅作用于 Final Answer/CLARIFY」三条约束。
- `README.md` 「角色语气定制」特性行：一句话体现结构化人格卡 + 情绪安全阀（不改篇幅结构）。
