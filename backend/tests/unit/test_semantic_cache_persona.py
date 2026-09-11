"""语义缓存 persona 分桶单元测试（persona-upgrade T2）。

覆盖：
- _bucket：role:persona 桶名，空 persona 回落 default
- 同 query 同 role 不同 persona 互不命中（L1 与 L2 均按 persona 隔离）
- 不传 persona 与旧行为一致：default 桶（l1:{role}:default:<hash> /
  l2:data:{role}:default）
- 带 persona 的 set 写入 persona 桶

mock 策略与 test_semantic_cache.py 一致：patch get_redis 返回 MagicMock。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.cache import semantic_cache
from app.core.cache.semantic_cache import (
    _bucket,
    _hash_query,
    _l1_key_for,
    _l2_data_key,
    get,
)
from app.core.cache.semantic_cache import (
    set as cache_set,
)


def _make_redis(hgetall_by_key=None):
    """构造 Redis mock：hgetall 按 key 精确路由（其余方法 AsyncMock）。"""
    redis_mock = MagicMock()
    redis_mock.is_connected = True
    redis_mock.get = AsyncMock(return_value="1")  # version=1
    redis_mock.hset = AsyncMock(return_value=True)
    redis_mock.client = MagicMock()

    async def _hgetall(key):
        if hgetall_by_key and key in hgetall_by_key:
            return hgetall_by_key[key]
        return {}

    redis_mock.hgetall = AsyncMock(side_effect=_hgetall)
    return redis_mock


# ---------- _bucket 桶名 ----------


def test_bucket_role_persona():
    assert _bucket("user", "lively") == "user:lively"
    assert _bucket("agent", "professional") == "agent:professional"


def test_bucket_empty_persona_falls_back_to_default():
    assert _bucket("user", "") == "user:default"
    assert _bucket("admin", None) == "admin:default"


def test_keys_use_bucket():
    """L1/L2 key 按桶拼接；不传 persona 落 default 桶。"""
    assert _l1_key_for("查询", "user", "lively") == (
        f"assistmind:cache:l1:user:lively:{_hash_query('查询')}"
    )
    assert _l1_key_for("查询", "user") == (
        f"assistmind:cache:l1:user:default:{_hash_query('查询')}"
    )
    assert _l2_data_key("agent") == "assistmind:cache:l2:data:agent:default"
    assert _l2_data_key("agent", "gentle") == "assistmind:cache:l2:data:agent:gentle"


# ---------- persona 之间互不命中 ----------


async def test_same_query_different_persona_no_cross_hit_l1():
    """同 query 同 role：lively 桶有 L1 缓存，professional / default 桶均不命中。"""
    lively_key = _l1_key_for("华为多少钱", "user", "lively")
    redis_mock = _make_redis(
        {
            lively_key: {
                "answer": "活泼答案",
                "sources": "[]",
                "version": "1",
            }
        }
    )
    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock):
        hit = await get("华为多少钱", role="user", persona="lively")
        miss_professional = await get("华为多少钱", role="user", persona="professional")
        miss_default = await get("华为多少钱", role="user")

    assert hit is not None
    assert hit["answer"] == "活泼答案"
    assert hit["from_cache"] == "L1"
    assert miss_professional is None
    assert miss_default is None


async def test_l2_lookup_scoped_by_persona():
    """L2 语义检索同样按 persona 隔离：lively 桶可命中，professional 桶查不到。"""
    entry = {
        "answer": "L2 活泼答案",
        "sources": "[]",
        "version": "1",
        "embedding": json.dumps([1.0, 0.0]),
    }
    redis_mock = _make_redis(
        {_l2_data_key("user", "lively"): {"华为多少钱": json.dumps(entry, ensure_ascii=False)}}
    )
    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock), patch(
        "app.core.cache.semantic_cache.embed_one", new=AsyncMock(return_value=[1.0, 0.0])
    ):
        hit = await get("华为多少钱", role="user", persona="lively")
        miss = await get("华为多少钱", role="user", persona="professional")

    assert hit is not None
    assert hit["from_cache"] == "L2"
    assert hit["answer"] == "L2 活泼答案"
    assert miss is None  # professional 桶为空，embedding 相同也不跨桶命中


# ---------- 写入与向后兼容 ----------


async def test_set_with_persona_writes_persona_bucket():
    """set 带 persona：L1 与 L2 data 均写入 persona 桶。"""
    redis_mock = _make_redis()
    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock), patch(
        "app.core.cache.semantic_cache.embed_one",
        new=AsyncMock(return_value=[0.1, 0.2]),
    ):
        await cache_set("查询", "答案", [], role="user", persona="lively")

    l1_keys = [c.args[0] for c in redis_mock.hset.call_args_list]
    assert f"assistmind:cache:l1:user:lively:{_hash_query('查询')}" in l1_keys
    assert "assistmind:cache:l2:data:user:lively" in l1_keys


async def test_set_without_persona_writes_default_bucket():
    """set 不传 persona：写 default 桶（与旧行为语义一致，仅桶名多 default 段）。"""
    redis_mock = _make_redis()
    with patch("app.core.cache.semantic_cache.get_redis", return_value=redis_mock), patch(
        "app.core.cache.semantic_cache.embed_one",
        new=AsyncMock(return_value=[0.1, 0.2]),
    ):
        await cache_set("查询", "答案", [], role="agent")

    l1_keys = [c.args[0] for c in redis_mock.hset.call_args_list]
    assert f"assistmind:cache:l1:agent:default:{_hash_query('查询')}" in l1_keys
    assert "assistmind:cache:l2:data:agent:default" in l1_keys
