"""Reranker（BAAI/bge-reranker-v2-m3）。

失败降级：
- 模型加载失败：rerank_* 返回 None，调用方跳过重排用 RRF 结果
- 推理失败：通过断路器计数，连续失败 N 次后 Open
- 断路器 Open：直接返回 None，避免无谓调用
- RERANKER_ENABLED=False：完全跳过
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import get_settings
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
    for d, s in zip(docs, scores):
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
