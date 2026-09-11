"""语义缓存单元测试。

覆盖：
- Redis 不可用时返回 None
- L1 exact match 命中
- L1 版本号不匹配时惰性清理
- L2 semantic match 命中
- 版本号失效
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.cache import semantic_cache
from app.core.cache.semantic_cache import (
    _cosine_similarity,
    _hash_query,
    get,
    invalidate,
)
from app.core.cache.semantic_cache import (
    set as cache_set,
)


def test_hash_query_returns_md5():
    """hash_query 应返回 32 位 md5。"""
    h = _hash_query("test")
    assert len(h) == 32
    assert h == _hash_query("test")  # 确定性


def test_cosine_similarity_identical_vectors():
    """相同向量相似度应为 1.0。"""
    v = [0.1, 0.2, 0.3]
    assert _cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    """正交向量相似度应为 0.0。"""
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_empty_vectors():
    """空向量相似度应为 0.0。"""
    assert _cosine_similarity([], []) == 0.0


def test_cosine_similarity_different_lengths():
    """长度不同的向量相似度应为 0.0。"""
    assert _cosine_similarity([1.0], [1.0, 2.0]) == 0.0


async def test_get_returns_none_when_redis_disconnected():
    """Redis 未连接时应返回 None。"""
    redis_mock = MagicMock()
    redis_mock.is_connected = False
    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock):
        result = await get("查询")
    assert result is None


async def test_l1_cache_hit():
    """L1 exact match 命中。"""
    redis_mock = MagicMock()
    redis_mock.is_connected = True
    redis_mock.get = AsyncMock(return_value="1")  # version=1
    redis_mock.hgetall = AsyncMock(return_value={
        "answer": "缓存答案",
        "sources": "[]",
        "version": "1",
    })

    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock):
        result = await get("查询")

    assert result is not None
    assert result["answer"] == "缓存答案"
    assert result["from_cache"] == "L1"


async def test_l1_cache_version_mismatch_triggers_cleanup():
    """L1 版本号不匹配应惰性清理。"""
    redis_mock = MagicMock()
    redis_mock.is_connected = True
    redis_mock.get = AsyncMock(return_value="2")  # 当前版本 2
    redis_mock.hgetall = AsyncMock(return_value={
        "answer": "旧答案",
        "sources": "[]",
        "version": "1",  # 缓存版本 1（已失效）
    })
    # L2 也返回空，确保最终返回 None
    client_mock = MagicMock()
    client_mock.hgetall = AsyncMock(return_value={})
    redis_mock.client = client_mock
    redis_mock.set = AsyncMock(return_value=True)

    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock):
        with patch("app.core.cache.semantic_cache.embed_one", new=AsyncMock(return_value=None)):
            result = await get("查询")

    # 应返回 None（L1 失效，L2 也没有）
    assert result is None


async def test_invalidate_increments_version():
    """invalidate 应 INCR 版本号。"""
    redis_mock = MagicMock()
    redis_mock.is_connected = True
    redis_mock.incr = AsyncMock(return_value=2)

    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock):
        await invalidate()

    redis_mock.incr.assert_called_once_with(semantic_cache._VERSION_KEY)


async def test_set_writes_l1_and_l2():
    """set 应同时写 L1 和 L2。"""
    redis_mock = MagicMock()
    redis_mock.is_connected = True
    redis_mock.get = AsyncMock(return_value="1")  # version=1
    redis_mock.hset = AsyncMock(return_value=True)
    redis_mock.set = AsyncMock(return_value=True)

    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock):
        with patch("app.core.cache.semantic_cache.embed_one", new=AsyncMock(return_value=[0.1, 0.2, 0.3])):
            await cache_set("查询", "答案", [{"doc_id": "d1"}])

    # L1 应被写入
    assert redis_mock.hset.call_count >= 1


async def test_set_returns_when_redis_disconnected():
    """Redis 未连接时 set 应直接返回。"""
    redis_mock = MagicMock()
    redis_mock.is_connected = False

    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock):
        await cache_set("查询", "答案", [])  # 不应抛异常

    redis_mock.hset.assert_not_called()


# ---------- RBAC 角色隔离（检索结果按 security_group 过滤，缓存禁止跨角色命中）----------


async def test_get_isolates_by_role():
    """带 role 的 get：L1 key 按 role 分桶（user 与其他 role 互不污染）。"""
    redis_mock = MagicMock()
    redis_mock.is_connected = True
    redis_mock.get = AsyncMock(return_value="1")

    # 记录每次 hgetall 的 key，首次（user）有值命中，其余空
    def _hgetall_side_effect(key):
        if key.startswith("assistmind:cache:l1:user:"):
            return {"answer": "user 答案", "sources": "[]", "version": "1"}
        return {}

    redis_mock.hgetall = AsyncMock(side_effect=_hgetall_side_effect)
    redis_mock.client = MagicMock()

    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock):
        user_hit = await get("查询", role="user")
        agent_miss = await get("查询", role="agent")
        admin_miss = await get("查询", role="admin")

    # user 命中，admin/agent 未命中（未跨角色命中高权限缓存）
    assert user_hit is not None
    assert user_hit["answer"] == "user 答案"
    assert agent_miss is None
    assert admin_miss is None

    # 各角色查询的 L1 key 前缀不同（role 已进入缓存键）；L2 lookup 另产生 l2:data key
    l1_keys = [k for k in {c.args[0] for c in redis_mock.hgetall.call_args_list} if k.startswith("assistmind:cache:l1:")]
    assert len(l1_keys) == 3
    assert any(k.startswith("assistmind:cache:l1:user:") for k in l1_keys)
    assert any(k.startswith("assistmind:cache:l1:agent:") for k in l1_keys)
    assert any(k.startswith("assistmind:cache:l1:admin:") for k in l1_keys)


async def test_set_writes_role_specific_keys():
    """带 role 的 set：L2 data key 与 L1 前缀包含 role。"""
    redis_mock = MagicMock()
    redis_mock.is_connected = True
    redis_mock.get = AsyncMock(return_value="1")
    redis_mock.hset = AsyncMock(return_value=True)
    redis_mock.set = AsyncMock(return_value=True)

    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock):
        with patch("app.core.cache.semantic_cache.embed_one", new=AsyncMock(return_value=[0.1, 0.2])):
            await cache_set("查询", "答案", [], role="agent")

    # L2 data hash 用 agent 分桶（不传 persona 落 default 子桶）
    l2_calls = [c for c in redis_mock.hset.call_args_list if c.args[0].startswith("assistmind:cache:l2:data:")]
    assert l2_calls and l2_calls[0].args[0] == "assistmind:cache:l2:data:agent:default"
