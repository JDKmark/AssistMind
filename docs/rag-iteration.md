# AssistMind RAG 迭代手册（Bad Case 五步法落地）

> 目标：把 RAG 从「Demo 能用」推进到「生产可靠」。核心观点——RAG 的瓶颈不在模型，在**测量与检索**。
> 绝大多数失败始于检索而非生成（漏召回 / 误召回 / 排序错位），因此先分环节量化，再让坏样例回流成回归样本，形成数据驱动的迭代闭环。
> 本手册说明五步法在 AssistMind 系统内的具体操作路径，对应代码均已在仓库落地。

## 排查顺序（经验法则）

80% 的问题出在召回与分块。排查顺序：

1. **文档质量**：来源是否过时、矛盾、解析正常（PDF 等二进制是否入库成功）
2. **召回**：相关文档有没有被检索到（context_precision / 看 hit_docs）
3. **重排**：正确文档是否被挤到候选之外（RERANK_TOP_K 太小）
4. **上下文构建**：chunk 是否结构完整、标题/注释前缀是否注入
5. **Prompt 生成**：最后才怀疑模型（多数情况下模型背锅）

先用「三要素分开评测」定位，不要一上来就调 Prompt / 换模型。

## 三要素分开评测

只测「答案对不对」会掩盖问题。AssistMind 用 RAGAS 4 指标分环节看：

| 指标 | 回答的问题 | 对应环节 |
|---|---|---|
| context_precision / context_recall | 召回的相关片段对不对、全不全 | 检索端 |
| faithfulness | 答案是否基于检索片段、有无编造 | 生成端（幻觉） |
| answer_relevancy | 答案是否真正回答了问题 | 生成端（答没答对点） |

检索质量决定答案质量的上限：检索错了生成再强也补不回来。

## 五步法操作路径

### 1. 收 Case（前端反馈 + 行为）

- 聊天页每条完成回答下方有「有帮助 / 没帮助」+ 可选评论，提交 `POST /api/v1/feedback/`
  （payload 带 `conversation_id` / `trace_id` / `query` / `answer` / `sources` / `intent` 快照）；
- 反馈落 PostgreSQL `feedbacks` 表（`score` 1-5，`score<=2` 视为 bad case）；
- 前端展示的知识来源列表即检索证据，点踩时用户可见命中来源。

### 2. 分类（召回 / 排序 / 幻觉 / 拒答 / 缺失）

- 查询待分类样本（admin）：`GET /api/v1/feedback/?score=2&exported=false`
- 结合反馈里的 `query` / `answer` / `sources` 快照判断：

| 现象 | 判断 | 处置 |
|---|---|---|
| 答案引用了错误来源 / 来源与问题无关 | 召回或排序问题 | 查检索命中（见 3），调 chunk / 权重 / Rerank |
| 答案与来源一致但答非所问 | 回答理解问题 | 查改写 / Prompt / 意图路由 |
| 答案与来源不一致（编造） | 生成幻觉 | 查 Prompt 约束 / CRAG 门禁是否生效 |
| 直接「未找到相关文档」但库里有 | 拒答 / 召回缺失 | 查 query 改写、该片段分块是否结构完整 |
| 内容过时 / 矛盾 | 文档质量问题 | 更新知识库，删过时内容 |

### 3. 归因（结合 Trace 还原证据链）

**系统内可视化追溯（无需翻日志/看代码）**：
- **Admin 页 → 反馈与追溯**（仅 admin）：差评列表（评分/是否已回流筛选）→ 点「追溯」打开**会话时间线抽屉**——
  ① 意图路由 → ② 检索来源（每条命中分数 + 片段全文）→ ③ CRAG 决策（直接生成 / 被动改写重试 / 空检索短路）→
  ④ 回答全文 → ⑤ 证据链；
- ⑤ 提供「在 Langfuse 查看完整 trace」外链（trace_id 非空且已配置 Langfuse 时显示）；
- **Chat 页**：知识来源列表每条显示命中分数，「查看溯因」可展开片段全文。
  追溯快照随反馈提交入库：`feedback` 表存 `crag_action` / `degraded` / `sources`（含 score/text），
  因此即使 Langfuse 未启用，系统内仍可查看证据链。

**Langfuse 深度追溯**：每条 FAQ 问答都会生成 Langfuse 根 trace `chat_faq`（未配置 Langfuse Key 时旁路 no-op）。
`conversation_id` 是「问答 ↔ 反馈」的关联键，`trace_id` 是证据链入口：
  - 在 Langfuse 里用 `trace_id` 打开该问答的完整时间线：
    `query → 改写变体 → 检索来源(metadata: crag_action / degraded) → llm.call（生成 span）→ answer`；
  - `crag_action` 记录本次走了 `generate` / `rewrite_retry` / `no_result`（chat 已接入与
    `engine.answer()` 同一门禁：空检索不再带空上下文生成，防幻觉）。
- 难判断时用 A/B 对照：
  - 换成另一组检索结果后分数明显改善 → 检索问题；
  - 换 Prompt 后分数明显改善 → 生成问题。

### 4. 改造（对症下药）

- **检索**：混合检索（向量 + BM25 + RRF）与 Reranker 已内置；结构感知切块在
  `backend/app/core/rag/chunking.py`（SQL DDL / 表格 / 代码块 / YAML 前缀），改切块后需重灌库
  （`seed_*_kb.py --reset`）。
- **生成**：Prompt 已显式声明「仅基于检索结果作答 + 引用 [n] 可溯源」；空检索由 CRAG 短路为「未找到」。
- **文档**：补知识、清过时、PDF 等二进制解析入库（见 `backend/app/core/rag/parsers.py`）。

### 5. 回归（反灌评估集验证）

```powershell
cd backend

# ① 把低分反馈回流成评估集 bad case（eval_feedback.json，幂等，按 question 去重）
venv\Scripts\python.exe scripts/export_feedback_badcases.py [--limit 50]

# ② 对 bad case 跑回归（无 ground_truth 样本，run_eval 自动跳过 context_recall；
#    建议人工为 bad case 补 ground_truth 后再做正式事实性回归）
venv\Scripts\python.exe scripts/run_eval.py app/data/eval_feedback.json
```

- 改造前后各跑一次 ②，diff 分数即归因证据（同一批 bad case 不得回退）；
- 常规 / 对抗分组单独统计（bad case 标记为对抗样本，混在一起算平均会误导质量判断）；
- eval 集（`app/data/eval_qa.json` / `eval_mall_qa.json` / `eval_feedback.json`）就是系统的
  「数据驱动的迭代机制」底座：每次检索 / 切块 / Prompt 改动后跑全量回归，防止修 A 坏 B。

## 系统内已具备的能力速查

| 能力 | 位置 |
|---|---|
| 混合检索 + RRF | `app/core/rag/engine.py` |
| Cross-Encoder 重排 | `app/core/rag/reranker.py`（RERANK_TOP_K=20 候选 → 8 条截断） |
| CRAG 门禁（chat 与 answer 共用） | `app/core/rag/engine.py` `resolve_retrieval` / `should_rewrite_retry` |
| 结构感知切块 | `app/core/rag/chunking.py` |
| RAGAS 评测 | `scripts/run_eval.py`（4 指标 + 对抗分组） |
| 反馈收集 / 查询 | `app/api/feedback.py`（POST + admin GET） |
| FAQ 会话 trace | `app/api/chat.py` `_faq_trace_span`（chat_faq 根 trace） |
| 可视化追溯 | Admin 页「反馈与追溯」时间线抽屉 + Chat 页「查看溯因」（来源分数/片段全文/CRAG 决策/降级项 + Langfuse 跳转） |
| 低分回流评估集 | `scripts/export_feedback_badcases.py` |
| RBAC 检索过滤 | `engine.retrieve(role=...)`（security_group payload filter） |

## 常见误区

- **只调 Prompt 不测检索**：先看 context_precision / hit_docs，检索没召回，Prompt 救不回来；
- **只看平均分**：对抗样本单独统计；bad case 样本单独统计；平均分被大样本稀释会掩盖单点故障；
- **改了切块不重灌**：分块逻辑变化不生效（embedding / BM25 索引都是旧的），必须 `--reset` 重灌；
- **改完不回归**：修一个用例坏掉一堆在库样本是常见回退，评估集回归是最后一道闸。
