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
from app.core.infra.llm_factory import LLMUnavailableError, call_llm
from app.core.infra.qdrant import get_qdrant
from app.core.rag.bm25 import get_bm25
from app.core.rag.embedding import embed_async
from app.core.rag.reranker import rerank_async
from app.core.rag.critic import evaluate as crag_evaluate
from app.core.query_rewriter import rewrite as rewrite_query

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
    """Jaccard 去重。"""
    result: list[dict[str, Any]] = []
    for d in docs:
        is_dup = False
        d_words = set(d.get("text", "")[:200])
        for r in result:
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
            contexts = reranked
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
            "sources": [{doc_id, title, source, snippet}],
            "degraded": bool
        }
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
            "score": c.get("rerank_score", c.get("rrf_score", 0)),
        }
        for c in contexts
    ]

    history_text = ""
    if history:
        history_text = "\n".join(
            [f"{'用户' if h.get('role') == 'user' else '客服'}: {h.get('content', '')}"
             for h in history[-settings.MEMORY_WINDOW:]]
        )

    prompt = f"""你是 AssistMind 智能客服。根据以下检索结果回答用户问题。

检索结果：
{ctx_text}

{f"对话历史：{history_text}" if history_text else ""}

回答要求：
- 直接回答用户问题：先给出明确结论/直接答案，再补充必要的细节或步骤
- 回答的首句直接复述并回应问题中的关键措辞（如问题问"根因"，首句就以"根因是…"开头；问"如何恢复"，首句就以"恢复动作包括…"开头）
- 仅基于检索结果回答：绝对不要使用检索结果之外的领域知识、经验、常识、推测来补充细节；检索结果没有提到的细节，回答中也不要出现（不确定宁可不写）
- 检索结果部分覆盖时：先回答覆盖到的部分；未覆盖的部分明确说明"资料未提及"，不要用常识补全
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

    # 2. CRAG 决策
    crag = retrieval["crag"]
    contexts = retrieval["contexts"]

    if crag["action"] == "no_result":
        return {
            "query": query,
            "answer": "未找到相关文档，建议转人工客服或换个表述重试。",
            "sources": [],
            "rewrites": retrieval["rewrites"],
            "crag": crag,
            "degraded": retrieval["degraded"],
        }

    # P1 修复：rewrite_retry 重检索后必须重新检查 no_result
    if crag["action"] == "rewrite_retry" and not retrieval["rewrites"]["degraded"]:
        # 被动改写重检索一次
        retry_query = (
            retrieval["rewrites"]["variants"][0]
            if retrieval["rewrites"]["variants"]
            else query
        )
        retry_retrieval = await retrieve(retry_query, role=role)
        # 修复：重检索后 contexts 为空，直接返回"未找到"，避免错误生成
        if not retry_retrieval["contexts"]:
            return {
                "query": query,
                "answer": "未找到相关文档，建议转人工客服或换个表述重试。",
                "sources": [],
                "rewrites": retrieval["rewrites"],
                "crag": retry_retrieval["crag"],
                "degraded": retrieval["degraded"] + retry_retrieval["degraded"],
            }
        contexts = retry_retrieval["contexts"]
        crag = retry_retrieval["crag"]
        # 修复：重检索后必须重新检查 no_result
        if crag["action"] == "no_result":
            return {
                "query": query,
                "answer": "未找到相关文档，建议转人工客服或换个表述重试。",
                "sources": [],
                "rewrites": retrieval["rewrites"],
                "crag": crag,
                "degraded": retrieval["degraded"] + retry_retrieval["degraded"],
            }

    # 3. 生成
    gen = await generate(query, contexts, history)

    return {
        "query": query,
        "answer": gen["answer"],
        "sources": gen["sources"],
        "rewrites": retrieval["rewrites"],
        "crag": crag,
        "degraded": retrieval["degraded"] + (["llm"] if gen["degraded"] else []),
    }
