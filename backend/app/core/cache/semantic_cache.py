"""L2 语义缓存（基于 Redis + Embedding 相似度）。

缓存策略：
- L1（exact match）：Redis hash，key=hash(query)，O(1) 命中
- L2（semantic）：Redis sorted set，按 embedding 相似度检索，similarity >= 阈值则命中

版本号失效：
- invalidate() 通过 INCR scqa:kb:version 实现 O(1) 失效
- 旧条目 lookup 时惰性清理（版本号不匹配则跳过）

P1 修复：
- L1 exact match 预过滤：先查 L1，命中直接返回，避免 embedding 计算
- 旧版本条目惰性清理：lookup 时发现 version 不匹配则 ZREM 删除
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.config import get_settings
from app.core.infra.redis import get_redis
from app.core.infra.llm_factory import LLMUnavailableError
from app.core.rag.embedding import embed_one

logger = logging.getLogger(__name__)
settings = get_settings()

_VERSION_KEY = "assistmind:kb:version"
_L1_KEY_PREFIX = "assistmind:cache:l1:"  # hash(query) -> {answer, version, ttl}
_L2_KEY = "assistmind:cache:l2"  # sorted set: member=query, score=version
_L2_DATA_KEY = "assistmind:cache:l2:data"  # hash: query -> {answer, version, embedding_hash}


def _hash_query(query: str) -> str:
    return hashlib.md5(query.encode("utf-8")).hexdigest()


async def get(query: str) -> dict[str, Any] | None:
    """查询缓存。

    Returns:
        {"answer": str, "sources": [...], "from_cache": "L1"|"L2"} 或 None
    """
    redis = get_redis()
    if not redis.is_connected:
        return None

    try:
        current_version = await redis.get(_VERSION_KEY)
        current_version = int(current_version) if current_version else 0

        # L1: exact match
        l1_key = _L1_KEY_PREFIX + _hash_query(query)
        l1_data = await redis.hgetall(l1_key)
        if l1_data:
            cached_version = int(l1_data.get("version", "0"))
            if cached_version == current_version:
                logger.debug("[Cache] L1 命中: %s", query[:30])
                return {
                    "answer": l1_data.get("answer", ""),
                    "sources": json.loads(l1_data.get("sources", "[]")),
                    "from_cache": "L1",
                }
            # 版本不匹配，惰性清理
            await _l1_delete(l1_key)

        # L2: semantic match
        l2_entry = await _l2_lookup(query, current_version)
        if l2_entry:
            logger.debug("[Cache] L2 命中: %s", query[:30])
            return {
                "answer": l2_entry.get("answer", ""),
                "sources": json.loads(l2_entry.get("sources", "[]")),
                "from_cache": "L2",
            }
    except Exception as e:
        logger.warning("[Cache] get 失败: %s", e)
    return None


async def set(query: str, answer: str, sources: list[dict[str, Any]]) -> None:
    """写入缓存（同时写 L1 和 L2）。"""
    redis = get_redis()
    if not redis.is_connected:
        return

    try:
        current_version = await redis.get(_VERSION_KEY)
        current_version = int(current_version) if current_version else 0

        sources_json = json.dumps(sources, ensure_ascii=False)
        data = {
            "answer": answer,
            "sources": sources_json,
            "version": str(current_version),
        }

        # L1
        l1_key = _L1_KEY_PREFIX + _hash_query(query)
        await redis.hset(l1_key, data)
        await redis.set(l1_key, "", ttl=settings.REDIS_CACHE_TTL)  # 设置 TTL（占位）

        # L2: 写入 query + embedding（用于相似度检索）
        emb = await embed_one(query)
        if emb:
            emb_hash = hashlib.md5(json.dumps(emb).encode()).hexdigest()
            data["embedding"] = json.dumps(emb, ensure_ascii=False)
            data["embedding_hash"] = emb_hash
            # 存到 L2 data hash
            await redis.hset(_L2_DATA_KEY, {query: json.dumps(data, ensure_ascii=False)})
    except Exception as e:
        logger.warning("[Cache] set 失败: %s", e)


async def _l2_lookup(query: str, current_version: int) -> dict[str, Any] | None:
    """L2 语义检索：遍历 L2 data，计算 embedding 相似度。"""
    redis = get_redis()
    if not redis.is_connected:
        return None

    all_data = await redis.hgetall(_L2_DATA_KEY)
    if not all_data:
        return None

    try:
        query_emb = await embed_one(query)
        if not query_emb:
            return None
    except LLMUnavailableError:
        return None
    except Exception as e:
        logger.warning("[Cache] L2 lookup embedding 失败: %s", e)
        return None

    best_score = 0.0
    best_entry: dict[str, Any] | None = None
    best_query: str | None = None
    to_delete: list[str] = []

    for cached_query, entry_json in all_data.items():
        try:
            entry = json.loads(entry_json)
            # 版本检查
            if int(entry.get("version", "0")) != current_version:
                to_delete.append(cached_query)
                continue
            # 计算余弦相似度
            cached_emb = json.loads(entry.get("embedding", "[]"))
            if not cached_emb:
                continue
            score = _cosine_similarity(query_emb, cached_emb)
            if score > best_score:
                best_score = score
                best_entry = entry
                best_query = cached_query
        except Exception:
            to_delete.append(cached_query)

    # 惰性清理旧版本条目
    for q in to_delete:
        await _l2_delete(q)

    if best_score >= settings.SEMANTIC_CACHE_SIMILARITY and best_entry:
        return best_entry
    return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度（向量已归一化时等于点积）。"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _l1_delete(key: str) -> None:
    redis = get_redis()
    if not redis.is_connected:
        return
    # RedisClient 没有暴露 delete，这里用 client 直接操作
    client = redis.client
    if client:
        await client.delete(key)


async def _l2_delete(query: str) -> None:
    redis = get_redis()
    if not redis.is_connected:
        return
    client = redis.client
    if client:
        await client.hdel(_L2_DATA_KEY, query)


async def invalidate() -> None:
    """版本号失效（O(1)）：INCR 版本号，旧条目惰性清理。"""
    redis = get_redis()
    if not redis.is_connected:
        return
    await redis.incr(_VERSION_KEY)
    logger.info("[Cache] 已失效所有缓存（版本号 +1）")


async def purge() -> None:
    """运维兜底：清空所有缓存（SCAN 全清，仅在重建索引时使用）。"""
    redis = get_redis()
    if not redis.is_connected:
        return
    client = redis.client
    if not client:
        return
    # 删除所有 L1 key
    async for key in client.scan_iter(match=_L1_KEY_PREFIX + "*"):
        await client.delete(key)
    # 删除 L2 data
    await client.delete(_L2_DATA_KEY)
    logger.info("[Cache] 已清空所有缓存（purge）")
