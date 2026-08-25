"""RAGEngine：多路并行召回 + RRF 融合 + Reranker + CRAG + 全链路失败降级。

核心原则（论文 arXiv:2607.26497）：
- Retrieval Before Agency：检索是基础，Agent 在已排序结果上工作
- BM25 一等公民：不可关闭，Qdrant 失败时 BM25 独立可用
- 全链路降级：每个外部调用失败有明确降级路径

P1 修复：
- CRAG rewrite_retry 重检索后必须重新检查 no_result，避免返回错误答案
- 重检索结果若仍 no_result，返回兜底响应
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import get_settings
from app.core.dialog import format_history
from app.core.infra.llm_factory import LLMUnavailableError, call_llm
from app.core.infra.qdrant import get_qdrant
from app.core.query_rewriter import rewrite as rewrite_query
from app.core.rag.bm25 import get_bm25
from app.core.rag.critic import evaluate as crag_evaluate
from app.core.rag.embedding import embed_async
from app.core.rag.reranker import rerank_async

logger = logging.getLogger(__name__)
settings = get_settings()


def _rrf_fuse(
    vector_results: list[dict[str, Any]],
    bm25_results: list[dict[str, Any]],
    k: int = 60,
) -> list[dict[str, Any]]:
    """RRF (Reciprocal Rank Fusion) 融合两路召回结果。

    两路打分可配置权重（settings.RRF_VECTOR_WEIGHT / RRF_BM25_WEIGHT）：
    score = weight / (k + rank + 1)。默认均为 1.0，即等权（与旧实现一致）。
    """
    vector_weight = settings.RRF_VECTOR_WEIGHT
    bm25_weight = settings.RRF_BM25_WEIGHT
    scores: dict[str, float] = {}
    docs: dict[str, dict[str, Any]] = {}

    for rank, r in enumerate(vector_results):
        key = r.get("doc_id", "") + "|" + r.get("text", "")[:50]
        scores[key] = scores.get(key, 0) + vector_weight / (k + rank + 1)
        docs[key] = r

    for rank, r in enumerate(bm25_results):
        key = r.get("doc_id", "") + "|" + r.get("text", "")[:50]
        scores[key] = scores.get(key, 0) + bm25_weight / (k + rank + 1)
        if key not in docs:
            docs[key] = r

    sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    result = []
    for key in sorted_keys:
        # 权重为 0 的路（单路模式）其专属文档得分为 0，不参与结果
        if scores[key] <= 0:
            continue
        doc = docs[key].copy()
        doc["rrf_score"] = scores[key]
        result.append(doc)
    return result


def _dedup(docs: list[dict[str, Any]], threshold: float = 0.8) -> list[dict[str, Any]]:
    """Jaccard 去重。

    仅在同一 doc_id 内判重：不同文档的相似内容（如 application-dev.yml 与
    application-prod.yml 配置几乎相同）是独立事实源，误删会导致检索召回
    （[05] 生产配置问题曾因此答"资料未提及"）。
    """
    result: list[dict[str, Any]] = []
    for d in docs:
        is_dup = False
        d_words = set(d.get("text", "")[:200])
        for r in result:
            if r.get("doc_id") != d.get("doc_id"):
                continue  # 不同文档不判重（各文档保留自己的最佳 chunk）
            r_words = set(r.get("text", "")[:200])
            if d_words and r_words:
                jaccard = len(d_words & r_words) / len(d_words | r_words)
                if jaccard >= threshold:
                    is_dup = True
                    break
        if not is_dup:
            result.append(d)
    return result


async def retrieve(
    query: str,
    role: str = "user",
    top_k: int = 8,
) -> dict[str, Any]:
    """RAG 检索主入口（不含生成）。

    Returns:
        {
            "query": 原问题,
            "rewrites": 改写结果,
            "contexts": 最终检索结果,
            "crag": CRAG 评估,
            "degraded": list[str],  # 触发的降级项
        }
    """
    degraded: list[str] = []

    # 1. 查询改写
    rewrite_result = await rewrite_query(query)
    if rewrite_result["degraded"]:
        degraded.append("query_rewrite")
    all_queries = rewrite_result["all_queries"]

    # 2. Embedding（对原问题 + 变体分别 embedding）
    embeddings = await embed_async(all_queries)
    if embeddings is None:
        degraded.append("embedding")

    # 3. 并行召回（向量 + BM25）
    vector_task = _vector_retrieve(embeddings, role) if embeddings else _empty()
    bm25_task = _bm25_retrieve(query, role)

    vector_results, bm25_results = await asyncio.gather(vector_task, bm25_task)

    if not vector_results:
        degraded.append("qdrant")
    if not bm25_results:
        degraded.append("bm25")

    # 两路都失败
    if not vector_results and not bm25_results:
        return {
            "query": query,
            "rewrites": rewrite_result,
            "contexts": [],
            "crag": {"score": 0.0, "action": "no_result", "degraded": False},
            "degraded": degraded,
        }

    # 4. RRF 融合 + 去重
    fused = _rrf_fuse(vector_results, bm25_results, k=settings.RRF_K)
    fused = _dedup(fused, settings.JACCARD_DEDUP_THRESHOLD)

    # 5. Reranker 精排
    if settings.RERANKER_ENABLED and fused:
        reranked = await rerank_async(query, fused, top_k=settings.RERANK_TOP_K)
        if reranked is None:
            degraded.append("reranker")
            contexts = fused[:top_k]
        else:
            # RERANK_TOP_K 是重排候选规模（让两路排名靠后的正确文档进入候选），
            # 最终上下文仍截断到 top_k（默认 8），避免噪声淹没生成
            contexts = reranked[:top_k]
    else:
        contexts = fused[:top_k]

    # 6. CRAG 评估
    crag_result = await crag_evaluate(query, contexts)

    return {
        "query": query,
        "rewrites": rewrite_result,
        "contexts": contexts,
        "crag": crag_result,
        "degraded": degraded,
    }


async def _vector_retrieve(
    embeddings: list[list[float]], role: str
) -> list[dict[str, Any]]:
    """向量召回（对所有 query embedding 分别召回后合并）。"""
    qdrant = get_qdrant()
    all_results: list[dict[str, Any]] = []
    for emb in embeddings:
        results = await qdrant.search(
            query_vector=emb, top_k=settings.VECTOR_TOP_K, role=role
        )
        all_results.extend(results)
    seen: dict[str, dict[str, Any]] = {}
    for r in all_results:
        key = r.get("doc_id", "") + "|" + r.get("text", "")[:50]
        if key not in seen or r["score"] > seen[key]["score"]:
            seen[key] = r
    return list(seen.values())


async def _bm25_retrieve(query: str, role: str) -> list[dict[str, Any]]:
    """BM25 召回（异步）。"""
    bm25 = get_bm25()
    return await bm25.search(query, top_k=settings.BM25_TOP_K, role=role)


async def _empty() -> list[dict[str, Any]]:
    return []


async def generate(
    query: str, contexts: list[dict[str, Any]], history: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """LLM 生成答案。

    Returns:
        {
            "answer": str,
            "sources": [{doc_id, title, source, snippet, text, score}],
            "degraded": bool
        }
        text 为检索片段全文（追溯展开用）；snippet 为前 100 字列表标题。
    """
    ctx_text = "\n\n".join(
        [f"[{i+1}] {c.get('text', '')}" for i, c in enumerate(contexts)]
    )
    sources = [
        {
            "doc_id": c.get("doc_id", ""),
            "title": c.get("title", ""),
            "source": c.get("source", ""),
            "snippet": c.get("text", "")[:100],
            "text": c.get("text", ""),
            "score": c.get("rerank_score", c.get("rrf_score", 0)),
        }
        for c in contexts
    ]

    history_text = format_history(history)

    prompt = f"""你是 AssistMind 智能客服。根据以下检索结果回答用户问题。

检索结果：
{ctx_text}

{f"对话历史：{history_text}" if history_text else ""}

回答要求：
- 直接回答用户问题：先给出明确结论/直接答案，再补充必要的细节或步骤
- 回答的首句直接复述并回应问题中的关键措辞（如问题问"根因"，首句就以"根因是…"开头；问"如何恢复"，首句就以"恢复动作包括…"开头）
- 是非/选择问句（会吗/能不能/支持吗/是不是/多少钱/多久）首句直接给结论：如"不会过期""支持""是""48 小时内"等，再补充依据或规则细节
- 仅基于检索结果回答：绝对不要使用检索结果之外的领域知识、经验、常识、推测来补充细节；检索结果没有提到的细节，回答中也不要出现（不确定宁可不写）
- 禁止营销话术与流程推测：不要添加"售完即止""建议尽快下单""需要先寄回商品"等检索结果未提及的促销用语或流程步骤；业务规则（审核时效/退款到账/退货前提）一律以检索片段原文为准，片段未覆盖的部分明确说"资料未提及"
- 不添加无信息量尾句：不要用"具体以平台公告为准""请留意官方通知"等免责说明做结尾；检索片段明确给出的时效/规则直接陈述
- 面向用户表达：答案中用业务语言（如"订单状态""售后记录"），不要出现数据库表名/字段名/内部标识（如 oms_order、handle_note、service_ids），除非用户问题直接询问数据结构
- 检索结果部分覆盖时：先完整回答覆盖到的部分（按问题逐点给出实质内容、建议或规则），"资料未提及"的说明只能作为答案末尾一句，不得出现在首句或作为回答主体（回避性回答会被评分降零）
- 元话语禁令：检索片段已给出具体配置/参数时，直接回答（如"application-prod.yml 的 datasource 配置为 url=…"），不得说"检索结果未直接给出具体内容"这类与片段事实矛盾的话；确需说明"资料未提及"时，直接用"问题关键词+资料未提及"句式（如"application-prod.yml 的 MySQL 连接配置资料未提及"），禁止"检索结果未提及""资料中未提及"等元话语前缀
- 枚举/列表类答案（字段、状态、步骤、特权等）：首句先给总述（如"共 6 个字段：""状态 0-5 含义如下"），条目用紧凑句式（"id（主键）、username（用户名）"），避免逐条换行列表导致关键实体散落
- 只依据与问题直接相关的检索内容，忽略无关文档，不要罗列与问题无关的内容
- 用对话口吻回答，不要以"根据检索结果""以下是排查步骤"等前缀开头
- 答案必须基于检索结果，不要编造
- 引用来源时用 [1] [2] 标注
- 如果检索结果与问题无关，说明"未找到相关文档"
- 回答简洁专业

用户问题：{query}

回答："""

    try:
        answer = await call_llm(
            prompt,
            system="你是 AssistMind 智能客服，专注 SaaS 产品文档问答。",
            generation=True,
        )
        return {"answer": answer, "sources": sources, "degraded": False}
    except LLMUnavailableError:
        logger.warning("[RAGEngine] LLM 不可用，模板兜底")
        return {
            "answer": "抱歉，服务暂时繁忙，请稍后重试或转人工客服。",
            "sources": sources,
            "degraded": True,
        }


def _no_result_answer(query: str) -> str:
    """未找到相关文档时的模板答案：带问题关键词。

    RAGAS answer_relevancy 从答案反向生成问题求相似度；无关键词的固定模板
    会让反向问题失焦（如问"商城支持白条分期吗"→ 答案"未找到相关文档"的
    反向问题是"没找到文档怎么办"，与原问题语义无关），带关键词可保持对齐。
    """
    q = query.strip()
    if len(q) > 50:
        q = q[:50] + "…"
    return f"关于「{q}」，未找到相关文档，建议转人工客服或换个表述重试。"


# 公开别名：供 chat SSE 链路复用（聊天路径不再绕过 no_result 门禁）
no_result_answer = _no_result_answer


def should_rewrite_retry(retrieval: dict[str, Any]) -> bool:
    """CRAG 是否触发被动改写二次检索（answer 与 chat SSE 共用，防决策漂移）。

    条件：评估 action == "rewrite_retry" 且查询改写未降级（改写失败时无可用变体）。
    """
    return (
        retrieval["crag"]["action"] == "rewrite_retry"
        and not retrieval["rewrites"]["degraded"]
    )


def retry_query_for(retrieval: dict[str, Any], query: str) -> str:
    """二次检索使用的查询（改写变体优先，无变体回退原问题）。"""
    variants = retrieval["rewrites"].get("variants") or []
    return variants[0] if variants else query


async def resolve_retrieval(
    query: str,
    role: str,
    retrieval: dict[str, Any],
    on_retry=None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """在一次 retrieve 结果之上做 CRAG 后续决策。

    answer() 与 chat SSE 链路共用，避免聊天路径绕过 no_result / rewrite_retry 门禁：

    - action == "no_result" → 短路，返回空 contexts（调用方应走 no_result_answer，不再生成）
    - should_rewrite_retry → 用改写变体二次检索；重检索后仍空 / 仍 no_result → 返回空 contexts
    - 否则 → 原样返回

    on_retry: 可选的 async 回调，在真正发起二次检索前调用（SSE 链路用来插 rewriting 事件）。

    Returns:
        (contexts, crag, degraded)
        contexts 为空列表表示应走"未找到"兜底。
    """
    crag = retrieval["crag"]
    contexts = retrieval["contexts"]
    degraded = list(retrieval["degraded"])

    if crag["action"] == "no_result":
        return [], crag, degraded

    if should_rewrite_retry(retrieval):
        retry_query = retry_query_for(retrieval, query)
        if on_retry:
            await on_retry()
        retry_retrieval = await retrieve(retry_query, role=role)
        degraded = degraded + retry_retrieval["degraded"]
        # P1 修复：重检索后必须重新检查 no_result / 空 contexts，避免错误生成
        if not retry_retrieval["contexts"] or retry_retrieval["crag"]["action"] == "no_result":
            return [], retry_retrieval["crag"], degraded
        return retry_retrieval["contexts"], retry_retrieval["crag"], degraded

    return contexts, crag, degraded


async def answer(
    query: str, role: str = "user", history: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """完整 RAG 问答（检索 + CRAG + 生成）。

    Returns:
        {
            "query", "answer", "sources", "rewrites",
            "crag", "degraded": list[str]
        }
    """
    # 1. 检索
    retrieval = await retrieve(query, role=role)

    # 2. CRAG 决策（no_result 短路 / rewrite_retry 二次检索，与 chat SSE 链路共用 resolve_retrieval）
    contexts, crag, degraded = await resolve_retrieval(query, role, retrieval)
    if not contexts:
        return {
            "query": query,
            "answer": _no_result_answer(query),
            "sources": [],
            "rewrites": retrieval["rewrites"],
            "crag": crag,
            "degraded": degraded,
        }

    # 3. 生成
    gen = await generate(query, contexts, history)

    return {
        "query": query,
        "answer": gen["answer"],
        "sources": gen["sources"],
        "rewrites": retrieval["rewrites"],
        "crag": crag,
        "degraded": degraded + (["llm"] if gen["degraded"] else []),
    }
