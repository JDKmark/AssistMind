"""BM25 跨进程版本同步单元测试（kb-management-p0 Task 2）。

行为契约（spec「Requirement: BM25 跨进程版本同步」）：
- 变更方 mark_changed：INCR assistmind:kb:bm25:version 并同步本地 _seen_version
- 检索方 ensure_fresh（search 入口）：版本落后 → 后台惰性重载（scroll_all → build，
  原子替换索引）；重载期间旧索引继续服务、不重复触发；Redis 失败跳过检查
- build 对齐当前版本（key 不存在/Redis 失败视为 0），启动后不立即重载
- Qdrant 不可用（load 返回 []）时不推进 _seen_version，旧索引保留

Redis 全部 mock（fakeredis），Qdrant 拉取直接 patch _load_docs_from_qdrant，
不连任何真实服务。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import fakeredis
import pytest

from app.core.rag import bm25 as bm25_mod
from app.core.rag.bm25 import BM25Index


def _doc(doc_id: str, text: str) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "text": text,
        "title": doc_id,
        "source": "t.md",
        "security_group": ["user", "agent", "admin"],
    }


@pytest.fixture
def fake_redis(monkeypatch):
    """把 _get_version_redis 注入点替换为独立 FakeRedis（每测试隔离）。"""
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(bm25_mod, "_get_version_redis", lambda: r)
    return r


async def test_bm25_mark_changed_incr_version(fake_redis):
    """mark_changed 后 Redis 版本 +1 且本地 _seen_version 同步为新值。"""
    index = BM25Index()
    key = bm25_mod.BM25_VERSION_KEY
    assert int(fake_redis.get(key) or 0) == 0

    index.mark_changed()
    assert int(fake_redis.get(key)) == 1
    assert index._seen_version == 1

    index.mark_changed()
    assert int(fake_redis.get(key)) == 2
    assert index._seen_version == 2


async def test_bm25_ensure_fresh_reload_on_stale(fake_redis):
    """版本落后时 ensure_fresh 创建后台重载任务；完成后 search 可召回新文档。"""
    index = BM25Index()
    index.build([_doc("old", "连接池耗尽需要调大最大连接数")])
    # 模拟 worker 进程完成变更后 INCR（本进程未知）
    fake_redis.set(bm25_mod.BM25_VERSION_KEY, "7")

    new_docs = [
        _doc("old", "连接池耗尽需要调大最大连接数"),
        _doc("new", "退货规则支持七天无理由退货"),
    ]

    async def fake_load() -> list[dict[str, Any]]:
        return new_docs

    with patch.object(bm25_mod, "_load_docs_from_qdrant", fake_load):
        await index.ensure_fresh()
        # ensure_fresh 创建了后台重载任务（不阻塞当前请求）
        assert index._reload_task is not None
        assert not index._reload_task.done()
        await index._reload_task  # 等待后台重载完成

    assert index._seen_version == 7
    # 重载完成后 search 可召回新文档（此时版本一致，不再触发重载）
    results = await index.search("退货规则", top_k=5)
    assert any(r["doc_id"] == "new" for r in results)
    assert index._reload_task is None


async def test_bm25_reload_inprogress_uses_old_index(fake_redis):
    """重载进行中：并发 search 用旧索引正常返回，不新建重复重载任务。"""
    index = BM25Index()
    index.build([_doc("old", "连接池耗尽需要调大最大连接数")])
    fake_redis.set(bm25_mod.BM25_VERSION_KEY, "3")

    gate = asyncio.Event()
    entered = asyncio.Event()

    async def slow_load() -> list[dict[str, Any]]:
        entered.set()
        await gate.wait()
        return [_doc("new", "退货规则支持七天无理由退货")]

    with patch.object(bm25_mod, "_load_docs_from_qdrant", slow_load):
        await index.ensure_fresh()
        task1 = index._reload_task
        assert task1 is not None
        await entered.wait()  # 重载已进入拉取阶段并被阻塞

        # 重载进行中：search 用旧索引正常返回（不阻塞、无异常）
        results = await index.search("连接池", top_k=5)
        assert [r["doc_id"] for r in results] == ["old"]

        # 重载任务未完成期间再 ensure_fresh：复用同一任务，不新建
        await index.ensure_fresh()
        assert index._reload_task is task1

        gate.set()
        await task1

    assert index._seen_version == 3


async def test_bm25_redis_down_search_unaffected(monkeypatch):
    """Redis 不可用（_get_version_redis 抛异常）：search 跳过版本检查正常返回。"""
    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(bm25_mod, "_get_version_redis", _boom)

    index = BM25Index()
    index.build([_doc("old", "连接池耗尽需要调大最大连接数")])
    results = await index.search("连接池", top_k=5)
    assert [r["doc_id"] for r in results] == ["old"]


async def test_bm25_build_sets_seen_version(fake_redis):
    """build 对齐当前 Redis 版本，后续 ensure_fresh 不触发重载。"""
    fake_redis.set(bm25_mod.BM25_VERSION_KEY, "9")
    index = BM25Index()
    index.build([_doc("d1", "连接池耗尽需要调大最大连接数")])
    assert index._seen_version == 9

    async def unexpected_load() -> list[dict[str, Any]]:
        raise AssertionError("版本一致时不应触发重载")

    with patch.object(bm25_mod, "_load_docs_from_qdrant", unexpected_load):
        await index.ensure_fresh()
    assert index._reload_task is None  # 未创建重载任务


async def test_bm25_reload_qdrant_unavailable_keeps_version(fake_redis):
    """_load_docs 返回 []（Qdrant 不可用）：不推进 _seen_version，旧索引保留。"""
    index = BM25Index()
    index.build([_doc("old", "连接池耗尽需要调大最大连接数")])
    fake_redis.set(bm25_mod.BM25_VERSION_KEY, "4")

    async def empty_load() -> list[dict[str, Any]]:
        return []

    with patch.object(bm25_mod, "_load_docs_from_qdrant", empty_load):
        await index.ensure_fresh()
        task = index._reload_task
        assert task is not None
        await task

    # 版本不推进（下次检索重试）、旧索引继续服务
    assert index._seen_version == 0
    old = index._search_sync("连接池", top_k=5, role="user")
    assert [r["doc_id"] for r in old] == ["old"]


async def test_bm25_skips_disabled_docs(fake_redis):
    """停用文档（enabled=False）不参与 BM25 打分，search 只返回启用篇。"""
    enabled_doc = _doc("on", "退货规则支持七天无理由退货")
    disabled_doc = _doc("off", "退货规则支持七天无理由退货")
    disabled_doc["enabled"] = False

    index = BM25Index()
    index.build([enabled_doc, disabled_doc])

    results = await index.search("退货规则", top_k=5)
    assert [r["doc_id"] for r in results] == ["on"]


async def test_bm25_missing_enabled_treated_as_enabled(fake_redis):
    """存量文档 payload 缺 enabled 字段视为启用，正常命中不被过滤。"""
    legacy_doc = _doc("legacy", "连接池耗尽需要调大最大连接数")
    assert "enabled" not in legacy_doc  # P0 之前灌库的 chunk 无该字段

    index = BM25Index()
    index.build([legacy_doc])

    results = await index.search("连接池", top_k=5)
    assert [r["doc_id"] for r in results] == ["legacy"]
