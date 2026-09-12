"""retrieve_raw（召回测试 dry-run）单元测试。

覆盖：
- 正常命中：两路都有结果，fused 非空且 rrf_score 按 rank 计算，响应含全部 5 个 key
- 候选规模：qdrant.search 收到 top_k*4
- embedding 失败：vector 空、degraded 标注、bm25 路仍有结果
- 两路均空：fused 空、不抛异常（200 语义）
- 两路均异常：三空数组 + degraded 标注，不抛异常

全 mock，不连外部服务。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import get_settings
from app.core.rag import engine

settings = get_settings()

VECTOR_RESULTS = [
    {
        "doc_id": "doc-v1",
        "text": "向量召回文档 A：退货政策说明",
        "title": "售后政策",
        "section_title": "退货",
        "score": 0.92,
        "source": "mall/refund.md",
    },
    {
        "doc_id": "doc-v2",
        "text": "向量召回文档 B：物流时效说明",
        "title": "物流",
        "section_title": "",
        "score": 0.71,
        "source": "mall/logistics.md",
    },
]

BM25_RESULTS = [
    {
        "doc_id": "doc-b1",
        "text": "BM25 召回文档 C：退款流程",
        "title": "退款",
        "section_title": "流程",
        "score": 3.7,
        "source": "mall/refund_flow.md",
    },
]


def _make_qdrant_mock(search_return=None, search_exc: Exception | None = None):
    qdrant_mock = MagicMock()
    qdrant_mock.is_connected = True
    if search_exc is not None:
        qdrant_mock.search = AsyncMock(side_effect=search_exc)
    else:
        qdrant_mock.search = AsyncMock(return_value=search_return or [])
    return qdrant_mock


def _make_bm25_mock(search_return=None, search_exc: Exception | None = None):
    bm25_mock = MagicMock()
    if search_exc is not None:
        bm25_mock.search = AsyncMock(side_effect=search_exc)
    else:
        bm25_mock.search = AsyncMock(return_value=search_return or [])
    return bm25_mock


def _patch_env(qdrant_mock, bm25_mock, embed_return=None, embed_exc: Exception | None = None):
    """统一 patch：engine.embed_async / get_qdrant / get_bm25。"""
    if embed_exc is not None:
        embed_mock = AsyncMock(side_effect=embed_exc)
    else:
        embed_mock = AsyncMock(return_value=embed_return)
    return (
        patch("app.core.rag.engine.embed_async", new=embed_mock),
        patch("app.core.rag.engine.get_qdrant", return_value=qdrant_mock),
        patch("app.core.rag.engine.get_bm25", return_value=bm25_mock),
    )


async def test_retrieve_raw_normal_hit_fused_with_rrf_score():
    """两路都有结果：fused 非空，rrf_score 按 rank 计算，响应含全部 5 个 key。"""
    qdrant_mock = _make_qdrant_mock(search_return=VECTOR_RESULTS)
    bm25_mock = _make_bm25_mock(search_return=BM25_RESULTS)
    p_embed, p_qdrant, p_bm25 = _patch_env(
        qdrant_mock, bm25_mock, embed_return=[[0.1, 0.2, 0.3]]
    )
    with p_embed, p_qdrant, p_bm25:
        result = await engine.retrieve_raw("退货政策是什么", top_k=5)

    assert set(result.keys()) == {"query", "vector", "bm25", "fused", "degraded"}
    assert result["query"] == "退货政策是什么"
    assert result["vector"] == VECTOR_RESULTS
    assert result["bm25"] == BM25_RESULTS
    assert result["degraded"] == []

    # 两路文档不相交，RRF 等权（默认 1.0）下 vector 第一名 = weight/(k+rank+1)
    expected_top = settings.RRF_VECTOR_WEIGHT / (settings.RRF_K + 0 + 1)
    assert result["fused"], "fused 应非空"
    top = result["fused"][0]
    assert top["doc_id"] == "doc-v1"
    assert top["rrf_score"] == pytest.approx(expected_top)
    # BM25 第一名同权重：其 rrf_score 也应为 weight/(k+1)
    b1 = [d for d in result["fused"] if d["doc_id"] == "doc-b1"][0]
    assert b1["rrf_score"] == pytest.approx(
        settings.RRF_BM25_WEIGHT / (settings.RRF_K + 0 + 1)
    )
    # 融合不做 _dedup / 重排 / CRAG：fused 是两路并集（2 + 1 条）
    assert len(result["fused"]) == 3


async def test_retrieve_raw_passes_top_k_times4_candidates():
    """候选规模：qdrant.search / bm25.search 应收到 top_k*4。"""
    qdrant_mock = _make_qdrant_mock(search_return=VECTOR_RESULTS)
    bm25_mock = _make_bm25_mock(search_return=BM25_RESULTS)
    p_embed, p_qdrant, p_bm25 = _patch_env(
        qdrant_mock, bm25_mock, embed_return=[[0.1, 0.2, 0.3]]
    )
    with p_embed, p_qdrant, p_bm25:
        await engine.retrieve_raw("退货政策", top_k=3)

    assert qdrant_mock.search.await_args.kwargs["top_k"] == 12
    assert bm25_mock.search.await_args.args[1] == 12
    assert qdrant_mock.search.await_args.kwargs["role"] == "user"
    assert bm25_mock.search.await_args.args[2] == "user"


async def test_retrieve_raw_embedding_fail_degrades_vector_bm25_still_works():
    """embed_async 返回 None：vector 空、degraded 含标注，bm25 路仍有结果。"""
    qdrant_mock = _make_qdrant_mock(search_return=VECTOR_RESULTS)
    bm25_mock = _make_bm25_mock(search_return=BM25_RESULTS)
    p_embed, p_qdrant, p_bm25 = _patch_env(qdrant_mock, bm25_mock, embed_return=None)
    with p_embed, p_qdrant, p_bm25:
        result = await engine.retrieve_raw("退货政策", top_k=5)

    assert result["vector"] == []
    assert "embedding" in result["degraded"]
    assert len(result["bm25"]) == 1
    # 仅 BM25 一路贡献，fused 仍非空且带 rrf_score
    assert len(result["fused"]) == 1
    assert result["fused"][0]["rrf_score"] == pytest.approx(
        settings.RRF_BM25_WEIGHT / (settings.RRF_K + 1)
    )


async def test_retrieve_raw_both_empty_returns_empty_without_raise():
    """两路均空：fused 空、degraded 标注两路，不抛异常（200 语义）。"""
    qdrant_mock = _make_qdrant_mock(search_return=[])
    bm25_mock = _make_bm25_mock(search_return=[])
    p_embed, p_qdrant, p_bm25 = _patch_env(
        qdrant_mock, bm25_mock, embed_return=[[0.1, 0.2, 0.3]]
    )
    with p_embed, p_qdrant, p_bm25:
        result = await engine.retrieve_raw("毫无关联的字符串", top_k=5)

    assert result["vector"] == []
    assert result["bm25"] == []
    assert result["fused"] == []
    assert "qdrant" in result["degraded"]
    assert "bm25" in result["degraded"]


async def test_retrieve_raw_both_raise_returns_empty_with_degraded():
    """两路均异常：返回三空数组 + degraded 标注，绝不抛异常。"""
    qdrant_mock = _make_qdrant_mock(search_exc=RuntimeError("qdrant down"))
    bm25_mock = _make_bm25_mock(search_exc=RuntimeError("bm25 down"))
    p_embed, p_qdrant, p_bm25 = _patch_env(
        qdrant_mock, bm25_mock, embed_return=[[0.1, 0.2, 0.3]]
    )
    with p_embed, p_qdrant, p_bm25:
        result = await engine.retrieve_raw("退货政策", top_k=5)  # 不应抛

    assert result["vector"] == []
    assert result["bm25"] == []
    assert result["fused"] == []
    assert result["degraded"]  # 至少有降级标注
