"""Reranker（BAAI/bge-reranker-v2-m3）。

RERANKER_PROVIDER：
- local：本机 CrossEncoder（sentence-transformers），CPU 推理慢
- siliconflow：SiliconFlow 云端 rerank API（免费档同款模型，分数同为 0-1）

失败降级（两条路径一致）：
- 模型加载失败 / API 失败 / 未配 key：rerank_* 返回 None，调用方跳过重排用 RRF 结果
- 推理失败：通过断路器计数，连续失败 N 次后 Open
- 断路器 Open：直接返回 None，避免无谓调用
- RERANKER_ENABLED=False：完全跳过
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.core.infra import fault_injection
from app.core.infra.circuit_breaker import (
    CircuitBreakerOpenError,
    call_with_breaker,
    is_open,
)

logger = logging.getLogger(__name__)
settings = get_settings()

_model: Any = None


def _get_model() -> Any:
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder

        _model = CrossEncoder(settings.RERANKER_MODEL)
        logger.info("[Reranker] 模型加载完成: %s", settings.RERANKER_MODEL)
    return _model


def _predict_sync(
    query: str, docs: list[dict[str, Any]], top_k: int
) -> list[dict[str, Any]]:
    """同步重排（在线程池中执行）。"""
    model = _get_model()
    pairs = [(query, d["text"]) for d in docs]
    scores = model.predict(pairs)
    for d, s in zip(docs, scores, strict=False):
        d["rerank_score"] = float(s)
    docs.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
    return docs[:top_k]


async def rerank_async(
    query: str, docs: list[dict[str, Any]], top_k: int = 8
) -> list[dict[str, Any]] | None:
    """异步重排。失败返回 None，调用方跳过重排。"""
    if not docs:
        return []
    if not settings.RERANKER_ENABLED:
        return None
    # 故障注入（T6）：命中即按既有降级契约返回 None，调用方跳过重排用 RRF
    if fault_injection.apply_reranker_fault():
        return None
    if settings.RERANKER_PROVIDER == "siliconflow":
        return await _rerank_via_siliconflow(query, docs, top_k)
    if is_open("reranker"):
        logger.warning("[Reranker] 断路器 Open，跳过重排")
        return None
    try:
        async def _do_rerank() -> list[dict[str, Any]]:
            return await asyncio.get_event_loop().run_in_executor(
                None, _predict_sync, query, docs, top_k
            )

        return await call_with_breaker("reranker", _do_rerank)
    except CircuitBreakerOpenError:
        logger.warning("[Reranker] rerank 断路器 Open")
        return None
    except Exception as e:
        logger.warning("[Reranker] rerank_async 失败（将跳过重排）: %s", e)
        return None


async def _rerank_via_siliconflow(
    query: str, docs: list[dict[str, Any]], top_k: int
) -> list[dict[str, Any]] | None:
    """SiliconFlow 云端 rerank（POST /v1/rerank，免费档 BAAI/bge-reranker-v2-m3）。

    relevance_score 与本地 CrossEncoder 同为 0-1 相关度，下游阈值语义不变；
    results[].index 指回入参 documents 下标。失败/未配 key 返回 None（跳过重排
    用 RRF），断路器与本地路径共用 "reranker"。
    """
    if not settings.SILICONFLOW_API_KEY:
        logger.warning(
            "[Reranker] RERANKER_PROVIDER=siliconflow 但未配置 SILICONFLOW_API_KEY，跳过重排"
        )
        return None
    if is_open("reranker"):
        logger.warning("[Reranker] 断路器 Open，跳过重排")
        return None

    async def _do_rerank() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=settings.RERANKER_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.SILICONFLOW_API_BASE}/rerank",
                headers={"Authorization": f"Bearer {settings.SILICONFLOW_API_KEY}"},
                json={
                    "model": settings.RERANKER_MODEL,
                    "query": query,
                    "documents": [d["text"] for d in docs],
                    "top_n": top_k,
                    "return_documents": False,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        ranked: list[dict[str, Any]] = []
        for item in payload.get("results", []):
            idx = item.get("index")
            if idx is None or not 0 <= idx < len(docs):
                continue
            doc = docs[idx]
            doc["rerank_score"] = float(item.get("relevance_score", 0.0))
            ranked.append(doc)
        ranked.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        logger.info(
            "[Reranker] SiliconFlow 重排完成：%d 候选 → top %d（最高分 %.3f）",
            len(docs),
            len(ranked),
            ranked[0]["rerank_score"] if ranked else 0.0,
        )
        return ranked

    try:
        # wait_for 硬护栏：httpx timeout 偶发失效时强制截断（≥RERANKER_TIMEOUT 后放弃重排）。
        # 超时 ascore 计入断路器（fail_max=3），连续失败后 Open → 后续直接跳过重排用 RRF。
        return await asyncio.wait_for(
            call_with_breaker("reranker", _do_rerank),
            timeout=settings.RERANKER_TIMEOUT + 5,
        )
    except CircuitBreakerOpenError:
        logger.warning("[Reranker] rerank 断路器 Open")
        return None
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("[Reranker] SiliconFlow rerank 超时（跳过重排，用 RRF 结果）")
        return None
    except Exception as e:
        logger.warning("[Reranker] SiliconFlow rerank 失败（将跳过重排）: %s", e)
        return None


def rerank_sync(
    query: str, docs: list[dict[str, Any]], top_k: int = 8
) -> list[dict[str, Any]] | None:
    """同步重排（用于脚本，不经过断路器）。"""
    if not docs:
        return []
    try:
        return _predict_sync(query, docs, top_k)
    except Exception as e:
        logger.warning("[Reranker] rerank_sync 失败: %s", e)
        return None
