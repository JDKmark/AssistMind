# phase7-chunk-orphan-title Spec

- Mode: lite
- Status: draft
- Created: 2026-08-08

## Why

mall 集 RAGAS 优化后（faithfulness 0.972 / cp 0.877 / cr 0.824），逐条对账 recall<0.7 的 9 个样本，发现检索链路仍有同类问题，根因集中在**切块层**：

1. **孤儿标题 chunk（51 个）**：`chunk_text` 对「标题行 + 紧跟结构元素（表格/fence）」处理时，标题行被 flush 成独立 chunk（如 `## 2. 订单状态流转` 单独成块），表格/fence 另起一块——标题 chunk 内容为空壳，浪费 top-8 检索槽位，且表格/列表脱离标题语义。
2. **GT 跨 chunk 事实**：GT 断言的多步事实（Windows 安装三步、Redis 三文件 host 配置）分散在多个 chunk，top-8 上下文覆盖不全 → recall=0/0.5。
3. **表格无上下文前缀**：表格块只有 `| 状态值 | 含义 |` 表头，无标题上下文，embedding/rerank 语义弱 → 排名低。

根因 ①②③ 都在 `chunk_text` 的 Markdown 分块逻辑（chunking.py:225-266）：
- 标题行先 `flush_text()` 更新 `current_section`，然后 `text_buf.append(line)` 把标题行加入缓冲；
- 下一行是表格/fence 时立即 `flush_text()` → 标题行单独成块。

目标：标题行与紧随的结构元素合并成块（标题作为块前缀），消除孤儿标题；表格块带 section 上下文（section_title 已带，但 text 内无标题行，embedding 只见表头）。

## What Changes

- `backend/app/core/rag/chunking.py`（`_chunk_markdown`）：标题行处理改为「记录 pending 标题，随下一个结构块/文本缓冲一起成块，不再单独 flush」；表格块 text 前拼接当前 section 标题行（`## {section}` 前缀）
- `backend/tests/unit/test_chunking.py`：更新/新增孤儿标题与表格前缀相关断言
- 重灌 mall 知识库（`seed_mall_kb.py --reset`，text 变化需重建向量）
- 重跑 mall 集 RAGAS（`run_eval.py app/data/eval_mall_qa.json`）验证 cr/faith 提升

## Impact

- 切块行为变化 → 已入库 chunk 的 text 变化 → **必须重灌**（Qdrant 向量 + BM25 索引）
- 检索语义增强（表格带标题前缀），预期 context_recall 提升；单测回归面：test_chunking.py
- 不涉及接口契约、前端零改动

## ADDED Requirements

### Requirement: 标题行并入结构块（无孤儿标题 chunk）
系统 SHALL 在 `chunk_text` 分块时，将标题行与其后紧跟的结构元素（表格/fence）或文本合并成块，不产生仅含标题行、无正文内容的 chunk。

#### Scenario: 标题后紧跟表格
- GIVEN Markdown 文本包含 `## 标题` 后紧跟表格行
- WHEN 调用 `chunk_text`
- THEN 产出 chunk 中不存在仅含 `## 标题` 的块，且表格块 text 以标题行开头（`## 标题\n| 状态值 |...`）

#### Scenario: 标题后跟普通文本
- GIVEN Markdown 文本包含 `## 标题` 后跟多行正文
- WHEN 调用 `chunk_text`
- THEN 标题行与正文在同一 chunk 中（或正文块以标题行为前缀），无孤儿标题

### Requirement: 表格块带标题上下文前缀
系统 SHALL 在表格块 text 前拼接当前 section 标题行（`## {section_title}`），使表格脱离标题后仍保留语义上下文。

#### Scenario: 表格单独成块
- GIVEN Markdown 文本包含 `## 订单状态` 后跟大表格（需按行边界切分为多块）
- WHEN 调用 `chunk_text`
- THEN 每块表格 text 以 `## 订单状态` 开头

## MODIFIED Requirements

（无）

## REMOVED Requirements

（无）

## RENAMED Requirements

（无）

## Tasks

- Task 1: 修改 `_chunk_markdown` 标题处理 + 表格前缀 → Req: 标题行并入结构块 / 表格块带标题上下文前缀
  - 1.1 标题行改为 pending 机制：标题行 flush 前检查当前 section 是否已有内容，无则并入下一块
  - 1.2 表格 emit_block 前拼接 `## {current_section}`
  - 1.3 更新 test_chunking.py 相关断言 + 新增孤儿标题/表格前缀用例
    Files: backend/app/core/rag/chunking.py, backend/tests/unit/test_chunking.py
    Run: cd backend; venv\Scripts\python.exe -m pytest tests/unit/test_chunking.py -q
    Expected: 全部通过，无孤儿标题 chunk（校验：mall KB 全库孤儿数 51 → 0）
- Task 2: 重灌 mall 知识库 → Req: 同上（验证切块变化入库）
  - 2.1 seed_mall_kb.py --reset 重灌（Qdrant 向量 + BM25）
    Files: 无代码改动
    Run: cd backend; venv\Scripts\python.exe scripts/seed_mall_kb.py --reset
    Expected: 334 chunks 灌库成功，孤儿标题 chunk 为 0
- Task 3: 重跑 mall 集 RAGAS → Req: 同上（验证指标）
  - 3.1 后台运行 run_eval.py app/data/eval_mall_qa.json，对比 ds5 基线
    Run: cd backend; venv\Scripts\python.exe scripts/run_eval.py app/data/eval_mall_qa.json
    Expected: context_recall ≥ 0.824（基线），目标 ≥ 0.85；faithfulness 不回退

# Task Dependencies
- Task 2 依赖 Task 1（先改切块再重灌）
- Task 3 依赖 Task 2

## 验收清单
- [ ] Scenario: 标题后紧跟表格 → chunk 无孤儿标题，表格块以标题开头
- [ ] Scenario: 标题后跟普通文本 → 无孤儿标题
- [ ] Scenario: 表格单独成块 → 每块带 `## 标题` 前缀
- [ ] test_chunking.py 全绿 + 全量单测无回归
- [ ] mall KB 孤儿标题 chunk 从 51 → 0
- [ ] RAGAS 重跑：context_recall ≥ 0.85（基线 0.824），faithfulness 不回退
