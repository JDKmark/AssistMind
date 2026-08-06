"""Embedding 客户端（BAAI/bge-base-zh-v1.5，768 维）。

失败降级：
- 模型加载失败：embed_* 返回 None，调用方降级为仅 BM25 召回
- 推理失败：通过断路器计数，连续失败 N 次后 Open
- 断路器 Open：直接返回 None，避免无谓调用

sentence-transformers 是同步库，用 run_in_executor 包裹避免阻塞事件循环。
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
    """懒加载 sentence-transformers 模型。"""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(
            settings.EMBEDDING_MODEL, device=settings.EMBEDDING_DEVICE
        )
        logger.info("[Embedding] 模型加载完成: %s", settings.EMBEDDING_MODEL)
    return _model


def _encode_sync(texts: list[str]) -> list[list[float]]:
    """同步 encoding（在线程池中执行）。"""
    model = _get_model()
    vecs = model.encode(texts, normalize_embeddings=True)
    return vecs.tolist()


async def embed_async(texts: list[str]) -> list[list[float]] | None:
    """异步 embedding（用于 API 调用）。

    通过断路器 + run_in_executor 调用，失败返回 None。
    """
    if not texts:
        return []
    if is_open("embedding"):
        logger.warning("[Embedding] 断路器 Open，跳过 embedding")
        return None
    try:
        async def _do_encode() -> list[list[float]]:
            return await asyncio.get_event_loop().run_in_executor(
                None, _encode_sync, texts
            )

        return await call_with_breaker("embedding", _do_encode)
    except CircuitBreakerOpenError:
        logger.warning("[Embedding] embedding 断路器 Open")
        return None
    except Exception as e:
        logger.warning("[Embedding] embed_async 失败（将降级为仅 BM25）: %s", e)
        return None


def embed_sync(texts: list[str]) -> list[list[float]] | None:
    """同步 embedding（用于脚本/初始化，不经过断路器）。"""
    try:
        return _encode_sync(texts)
    except Exception as e:
        logger.warning("[Embedding] embed_sync 失败: %s", e)
        return None


async def embed_one(text: str) -> list[float] | None:
    """单文本 embedding。"""
    result = await embed_async([text])
    return result[0] if result else None
