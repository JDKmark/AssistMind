"""L2 语义缓存（基于 Redis + Embedding 相似度）。

缓存策略：
- L1（exact match）：Redis hash，key=hash(query)，O(1) 命中
- L2（semantic）：Redis sorted set，按 embedding 相似度检索，similarity >= 阈值则命中

版本号失效：
- invalidate() 通过 INCR scqa:kb:version 实现 O(1) 失效
- 旧条目 lookup 时惰性清理（版本号不匹配则跳过）

RBAC 安全（角色隔离）：
- 检索结果按 security_group 过滤，不同 role（user/agent/admin）看到的内容不同；
  缓存必须按 role 隔离，禁止跨角色命中（否则低权限角色可能拿到高权限答案）。
- L1 前缀 / L2 集合均按 role 分桶：`assistmind:cache:l1:{role}:` 与
  `assistmind:cache:l2:{role}`；invalidate 版本号仍全局 O(1) 失效，purge 扫全量。

persona 分桶（选人格不再放弃缓存）：
- 人格改变答案语气，同一 query 不同 persona 的答案不可互串：桶维度从 {role}
  细化为 {role}:{persona or "default"}（`_bucket`）。不传 persona 时落 default
  桶，与旧"无 persona 才走缓存"的行为语义一致；旧格式条目（无 persona 段）
  升级后自然 miss，一次性冷启动，不做数据迁移。
- purge 的 SCAN 模式（assistmind:cache:l1:* / assistmind:cache:l2:data:*）
  天然覆盖新桶，无需调整。

P1 修复：
- L1 exact match 预过滤：先查 L1，命中直接返回，避免 embedding 计算
- 旧版本条目惰性清理：lookup 时发现 version 不匹配则 ZREM 删除
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from app.config import get_settings
from app.core.infra.llm_factory import LLMUnavailableError
from app.core.infra.redis import get_redis
from app.core.rag.embedding import embed_one

logger = logging.getLogger(__name__)
settings = get_settings()

_VERSION_KEY = "assistmind:kb:version"
_L1_KEY_PREFIX = "assistmind:cache:l1:"  # {role}:{persona}:hash(query) -> {answer, version, ttl}
_L2_KEY = "assistmind:cache:l2:{role}"  # sorted set: member=query, score=version
_L2_DATA_KEY = "assistmind:cache:l2:data:{role}"  # hash: query -> {answer, version, embedding_hash}


def _hash_query(query: str) -> str:
    return hashlib.md5(query.encode("utf-8")).hexdigest()


def _bucket(role: str, persona: str) -> str:
    """缓存桶维度：{role}:{persona}；空 persona 回落 default（与无 persona 行为一致）。"""
    return f"{role}:{persona or 'default'}"


def _l1_key_for(query: str, role: str, persona: str = "") -> str:
    return f"{_L1_KEY_PREFIX}{_bucket(role, persona)}:" + _hash_query(query)


def _l2_data_key(role: str, persona: str = "") -> str:
    return _L2_DATA_KEY.format(role=_bucket(role, persona))


async def get(query: str, role: str = "user", persona: str = "") -> dict[str, Any] | None:
    """查询缓存。

    role: 检索 RBAC 角色（user/agent/admin）。缓存按角色隔离，
        默认 user 保持向后兼容（现有调用方无角色时行为不变）。
    persona: 客服人格 id（空串/None 落 default 桶）。不同 persona 语气不同，
        同 query 的答案不可互串，禁止跨 persona 命中。

    Returns:
        {"answer": str, "sources": [...], "from_cache": "L1"|"L2"} 或 None
    """
    redis = get_redis()
    if not redis.is_connected:
        return None

    try:
        current_version = await redis.get(_VERSION_KEY)
        current_version = int(current_version) if current_version else 0

        # L1: exact match（按 role + persona 隔离）
        l1_key = _l1_key_for(query, role, persona)
        l1_data = await redis.hgetall(l1_key)
        if l1_data:
            cached_version = int(l1_data.get("version", "0"))
            expires_at = int(l1_data.get("_expires_at", "0"))
            if expires_at and time.time() > expires_at:
                # TTL 过期，惰性清理
                await _l1_delete(l1_key)
            elif cached_version == current_version:
                logger.debug("[Cache] L1 命中: %s", query[:30])
                return {
                    "answer": l1_data.get("answer", ""),
                    "sources": json.loads(l1_data.get("sources", "[]")),
                    "from_cache": "L1",
                }
            else:
                # 版本不匹配，惰性清理
                await _l1_delete(l1_key)

        # L2: semantic match（同 role + persona 桶内按相似度检索）
        l2_entry = await _l2_lookup(query, role, current_version, persona=persona)
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


async def set(
    query: str,
    answer: str,
    sources: list[dict[str, Any]],
    role: str = "user",
    persona: str = "",
) -> None:
    """写入缓存（同时写 L1 和 L2，按 role + persona 分桶）。"""
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

        # L1（hash，_expires_at 字段控制 TTL；勿用 redis.set 同 key 写 string，会覆盖 hash）
        l1_key = _l1_key_for(query, role, persona)
        data["_expires_at"] = str(int(time.time()) + settings.REDIS_CACHE_TTL)
        await redis.hset(l1_key, data)

        # L2: 写入 query + embedding（用于相似度检索，按 role + persona 分桶）
        emb = await embed_one(query)
        if emb:
            emb_hash = hashlib.md5(json.dumps(emb).encode()).hexdigest()
            data["embedding"] = json.dumps(emb, ensure_ascii=False)
            data["embedding_hash"] = emb_hash
            await redis.hset(_l2_data_key(role, persona), {query: json.dumps(data, ensure_ascii=False)})
    except Exception as e:
        logger.warning("[Cache] set 失败: %s", e)


async def _l2_lookup(
    query: str, role: str, current_version: int, persona: str = ""
) -> dict[str, Any] | None:
    """L2 语义检索：遍历当前 role + persona 桶的 L2 data，计算 embedding 相似度。"""
    redis = get_redis()
    if not redis.is_connected:
        return None

    all_data = await redis.hgetall(_l2_data_key(role, persona))
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
        except Exception:
            to_delete.append(cached_query)

    # 惰性清理旧版本条目
    for q in to_delete:
        await _l2_delete(q, role, persona=persona)

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


async def _l2_delete(query: str, role: str = "user", persona: str = "") -> None:
    redis = get_redis()
    if not redis.is_connected:
        return
    client = redis.client
    if client:
        await client.hdel(_l2_data_key(role, persona), query)


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
    # 删除所有 L1 key（含各 role 分桶）
    async for key in client.scan_iter(match=_L1_KEY_PREFIX + "*"):
        await client.delete(key)
    # 删除各 role 的 L2 data
    async for key in client.scan_iter(match="assistmind:cache:l2:data:*"):
        await client.delete(key)
    await client.delete("assistmind:cache:l2")
    logger.info("[Cache] 已清空所有缓存（purge）")
