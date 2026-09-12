"""Qdrant 客户端单测（不连真实 Qdrant）。

覆盖：
- upsert payload 携带 chunk_index（显式值 / 缺省用枚举下标）
- scroll_by_doc：filter 条件、分页合并、chunk_index 缺省补 -1、排序（-1 排最后）、降级契约
- set_payload_by_doc：传参契约（FilterSelector doc_id）+ 降级契约（与 delete_by_doc 一致）
- search：query_filter 含 must_not enabled=False（存量数据缺字段视为启用）
- scroll_all / scroll_by_doc：enabled 透传 / 缺省 True

mock 策略：QdrantClient._client 替换为 AsyncMock；断路器 is_open / call_with_breaker
在 qdrant 模块命名空间内 patch（is_open=False 直通）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from qdrant_client.http import models

from app.config import get_settings
from app.core.infra.circuit_breaker import CircuitBreakerOpenError
from app.core.infra.qdrant import QdrantClient

MODULE = "app.core.infra.qdrant"
settings = get_settings()


def _record(payload: dict) -> SimpleNamespace:
    """模拟 scroll 返回的 Record（只用得到 payload）。"""
    return SimpleNamespace(payload=payload)


@pytest.fixture
def client() -> QdrantClient:
    c = QdrantClient()
    c._client = AsyncMock()
    return c


@pytest.fixture(autouse=True)
def breaker_passthrough(monkeypatch):
    """默认断路器闭合 + call_with_breaker 直通；个别用例自行覆盖。"""
    monkeypatch.setattr(f"{MODULE}.is_open", lambda _name: False)

    async def _passthrough(_name, fn, **kwargs):
        return await fn(**kwargs)

    monkeypatch.setattr(f"{MODULE}.call_with_breaker", _passthrough)


# ---------------------------------------------------------------------------
# upsert：payload 含 chunk_index
# ---------------------------------------------------------------------------


async def test_upsert_payload_includes_chunk_index(client):
    """chunk 显式带 chunk_index 时，payload 原样携带。"""
    chunks = [{"doc_id": "d1", "text": "t", "chunk_index": 3}]
    ok = await client.upsert(chunks, [[0.1, 0.2]])
    assert ok is True
    point = client._client.upsert.call_args.kwargs["points"][0]
    assert point.payload["chunk_index"] == 3


async def test_upsert_payload_chunk_index_defaults_to_enum_idx(client):
    """chunk 缺 chunk_index 时，payload 用 enumerate 下标补齐（与 id 生成一致）。"""
    chunks = [{"doc_id": "d1", "text": "a"}, {"doc_id": "d1", "text": "b"}]
    ok = await client.upsert(chunks, [[0.1], [0.2]])
    assert ok is True
    points = client._client.upsert.call_args.kwargs["points"]
    assert [p.payload["chunk_index"] for p in points] == [0, 1]


# ---------------------------------------------------------------------------
# scroll_by_doc：filter / 分页 / 兼容 / 排序 / 降级
# ---------------------------------------------------------------------------


async def test_scroll_by_doc_filter_condition(client):
    """filter 必须是 doc_id 精确匹配（FieldCondition + MatchValue）。"""
    client._client.scroll.return_value = ([], None)
    await client.scroll_by_doc("uploads/退货规则")
    kwargs = client._client.scroll.call_args.kwargs
    flt = kwargs["scroll_filter"]
    assert isinstance(flt, models.Filter)
    cond = flt.must[0]
    assert isinstance(cond, models.FieldCondition)
    assert cond.key == "doc_id"
    assert cond.match == models.MatchValue(value="uploads/退货规则")
    assert kwargs["limit"] == 1000
    assert kwargs["with_payload"] is True
    assert kwargs["with_vectors"] is False


async def test_scroll_by_doc_pagination_merges_batches(client):
    """多批 scroll 按 offset 推进合并，next_offset=None 终止。"""
    batch1 = [_record({"doc_id": "d1", "chunk_index": 0, "text": "a"})]
    batch2 = [
        _record({"doc_id": "d1", "chunk_index": 1, "text": "b"}),
        _record({"doc_id": "d1", "chunk_index": 2, "text": "c"}),
    ]
    client._client.scroll.side_effect = [(batch1, "offset-1"), (batch2, None)]

    result = await client.scroll_by_doc("d1")

    assert client._client.scroll.call_count == 2
    assert client._client.scroll.call_args_list[0].kwargs["offset"] is None
    assert client._client.scroll.call_args_list[1].kwargs["offset"] == "offset-1"
    assert [r["chunk_index"] for r in result] == [0, 1, 2]
    assert [r["text"] for r in result] == ["a", "b", "c"]


async def test_scroll_by_doc_element_fields(client):
    """元素字段契约：chunk_index/doc_id/title/source/category/section_title/table_comment/text/enabled。"""
    payload = {
        "doc_id": "d1",
        "chunk_index": 0,
        "title": "标题",
        "source": "s.md",
        "category": "mall",
        "section_title": "小节",
        "table_comment": "注释",
        "text": "正文",
        "security_group": ["user"],
    }
    client._client.scroll.return_value = ([_record(payload)], None)
    result = await client.scroll_by_doc("d1")
    assert result == [
        {
            "chunk_index": 0,
            "doc_id": "d1",
            "title": "标题",
            "source": "s.md",
            "category": "mall",
            "section_title": "小节",
            "table_comment": "注释",
            "text": "正文",
            "enabled": True,
        }
    ]


async def test_scroll_by_doc_missing_chunk_index_defaults_neg1(client):
    """存量数据 payload 缺 chunk_index 时补 -1。"""
    client._client.scroll.return_value = (
        [_record({"doc_id": "d1", "text": "legacy"})],
        None,
    )
    result = await client.scroll_by_doc("d1")
    assert result[0]["chunk_index"] == -1


async def test_scroll_by_doc_sorts_by_chunk_index_neg1_last(client):
    """按 chunk_index 升序；-1（存量缺字段）排最后，同 -1 保持稳定顺序。"""
    client._client.scroll.return_value = (
        [
            _record({"doc_id": "d1", "chunk_index": 2, "text": "c"}),
            _record({"doc_id": "d1", "chunk_index": -1, "text": "legacy-1"}),
            _record({"doc_id": "d1", "chunk_index": 0, "text": "a"}),
            _record({"doc_id": "d1", "chunk_index": -1, "text": "legacy-2"}),
        ],
        None,
    )
    result = await client.scroll_by_doc("d1")
    assert [r["chunk_index"] for r in result] == [0, 2, -1, -1]
    assert [r["text"] for r in result] == ["a", "c", "legacy-1", "legacy-2"]


async def test_scroll_by_doc_not_connected_returns_empty():
    """未连接（_client 为 None）返回 []，不抛异常。"""
    c = QdrantClient()
    assert await c.scroll_by_doc("d1") == []


async def test_scroll_by_doc_breaker_open_returns_empty(client, monkeypatch):
    """断路器 Open 直接返回 []，不触底层数据源。"""
    monkeypatch.setattr(f"{MODULE}.is_open", lambda _name: True)
    assert await client.scroll_by_doc("d1") == []
    client._client.scroll.assert_not_called()


async def test_scroll_by_doc_circuit_breaker_open_error_returns_empty(client, monkeypatch):
    """call_with_breaker 抛 CircuitBreakerOpenError 返回 []。"""

    async def _raise(_name, fn, **kwargs):
        raise CircuitBreakerOpenError("open")

    monkeypatch.setattr(f"{MODULE}.call_with_breaker", _raise)
    assert await client.scroll_by_doc("d1") == []


async def test_scroll_by_doc_unexpected_error_returns_empty(client, monkeypatch):
    """其他异常 logger.warning 后返回 []，不向上抛。"""
    client._client.scroll.side_effect = RuntimeError("boom")
    assert await client.scroll_by_doc("d1") == []


# ---------------------------------------------------------------------------
# set_payload_by_doc：批量 payload 写入（文档启停切换底层）
# ---------------------------------------------------------------------------


async def test_set_payload_by_doc_passes_contract(client):
    """透传 payload + FilterSelector 按 doc_id 精确匹配 + collection 名。"""
    ok = await client.set_payload_by_doc("d1", {"enabled": False})
    assert ok is True
    kwargs = client._client.set_payload.call_args.kwargs
    assert kwargs["collection_name"] == settings.QDRANT_COLLECTION
    assert kwargs["payload"] == {"enabled": False}
    selector = kwargs["points_selector"]
    assert isinstance(selector, models.FilterSelector)
    cond = selector.filter.must[0]
    assert isinstance(cond, models.FieldCondition)
    assert cond.key == "doc_id"
    assert cond.match == models.MatchValue(value="d1")


async def test_set_payload_by_doc_not_connected_returns_false():
    """未连接（_client 为 None）返回 False，不抛异常。"""
    c = QdrantClient()
    assert await c.set_payload_by_doc("d1", {"enabled": False}) is False


async def test_set_payload_by_doc_breaker_open_returns_false(client, monkeypatch):
    """断路器 Open 直接返回 False，不触底层数据源。"""
    monkeypatch.setattr(f"{MODULE}.is_open", lambda _name: True)
    assert await client.set_payload_by_doc("d1", {"enabled": False}) is False
    client._client.set_payload.assert_not_called()


async def test_set_payload_by_doc_circuit_breaker_open_error_returns_false(client, monkeypatch):
    """call_with_breaker 抛 CircuitBreakerOpenError 返回 False。"""

    async def _raise(_name, fn, **kwargs):
        raise CircuitBreakerOpenError("open")

    monkeypatch.setattr(f"{MODULE}.call_with_breaker", _raise)
    assert await client.set_payload_by_doc("d1", {"enabled": False}) is False


async def test_set_payload_by_doc_unexpected_error_returns_false_with_warning(client, monkeypatch, caplog):
    """其他异常 logger.warning 后返回 False，不向上抛。"""
    client._client.set_payload.side_effect = RuntimeError("boom")
    with caplog.at_level("WARNING", logger="app.core.infra.qdrant"):
        assert await client.set_payload_by_doc("d1", {"enabled": False}) is False
    assert any("set_payload_by_doc" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# search：filter 排除 enabled=False（存量数据缺字段视为启用）
# ---------------------------------------------------------------------------


def _search_response(points):
    return SimpleNamespace(points=points)


async def test_search_filter_excludes_disabled_docs(client):
    """query_filter：must 含 security_group 条件，must_not 排除 enabled=False。"""
    client._client.query_points.return_value = _search_response([])
    await client.search([0.1, 0.2], top_k=5, role="user")
    flt = client._client.query_points.call_args.kwargs["query_filter"]
    assert isinstance(flt, models.Filter)
    rbac = flt.must[0]
    assert isinstance(rbac, models.FieldCondition)
    assert rbac.key == "security_group"
    assert rbac.match == models.MatchAny(any=["user"])
    assert len(flt.must_not) == 1
    disabled = flt.must_not[0]
    assert isinstance(disabled, models.FieldCondition)
    assert disabled.key == "enabled"
    assert disabled.match == models.MatchValue(value=False)


# ---------------------------------------------------------------------------
# scroll_all / scroll_by_doc：enabled 字段透传与缺省
# ---------------------------------------------------------------------------


async def test_scroll_all_enabled_false_passthrough(client):
    """payload 带 enabled=False 时透传 False。"""
    client._client.scroll.return_value = (
        [_record({"doc_id": "d1", "text": "a", "enabled": False})],
        None,
    )
    result = await client.scroll_all()
    assert result[0]["enabled"] is False


async def test_scroll_all_enabled_missing_defaults_true(client):
    """存量数据 payload 缺 enabled 时默认 True。"""
    client._client.scroll.return_value = (
        [_record({"doc_id": "d1", "text": "legacy"})],
        None,
    )
    result = await client.scroll_all()
    assert result[0]["enabled"] is True


async def test_scroll_by_doc_enabled_false_passthrough(client):
    """payload 带 enabled=False 时透传 False。"""
    client._client.scroll.return_value = (
        [_record({"doc_id": "d1", "chunk_index": 0, "text": "a", "enabled": False})],
        None,
    )
    result = await client.scroll_by_doc("d1")
    assert result[0]["enabled"] is False
