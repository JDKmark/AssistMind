"""RRF 融合权重（RRF_VECTOR_WEIGHT / RRF_BM25_WEIGHT）单元测试。

覆盖：
- 默认等权（1.0/1.0）下融合结果与旧实现（无权重）完全一致
- 修改权重后融合排序随权重变化（真实断言排序翻转）
- 权重为 0 时该路不参与，结果等价于单路融合
"""

from __future__ import annotations

import pytest

from app.core.rag import engine


def _doc(doc_id: str, text: str) -> dict:
    return {"doc_id": doc_id, "text": text, "title": f"t-{doc_id}", "source": "s"}


def _legacy_rrf(vector_results, bm25_results, k=60):
    """旧实现（无权重）：score = 1.0 / (k + rank + 1)。"""
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    for rank, r in enumerate(vector_results):
        key = r.get("doc_id", "") + "|" + r.get("text", "")[:50]
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        docs[key] = r

    for rank, r in enumerate(bm25_results):
        key = r.get("doc_id", "") + "|" + r.get("text", "")[:50]
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        if key not in docs:
            docs[key] = r

    sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    result = []
    for key in sorted_keys:
        doc = docs[key].copy()
        doc["rrf_score"] = scores[key]
        result.append(doc)
    return result


def test_rrf_default_weights_match_legacy(monkeypatch) -> None:
    """默认等权（1.0/1.0）下融合顺序与 rrf_score 应与旧实现完全一致。"""
    # 显式置为默认值，避免 .env / 环境变量干扰测试确定性
    monkeypatch.setattr(engine.settings, "RRF_VECTOR_WEIGHT", 1.0)
    monkeypatch.setattr(engine.settings, "RRF_BM25_WEIGHT", 1.0)

    vector_results = [
        _doc("a", "文档 A 内容"),
        _doc("b", "文档 B 内容"),
        _doc("c", "文档 C 内容"),
    ]
    bm25_results = [
        _doc("b", "文档 B 内容"),
        _doc("d", "文档 D 内容"),
        _doc("a", "文档 A 内容"),
        _doc("e", "文档 E 内容"),
    ]

    fused = engine._rrf_fuse(vector_results, bm25_results, k=60)
    expected = _legacy_rrf(vector_results, bm25_results, k=60)

    assert [d["doc_id"] for d in fused] == [d["doc_id"] for d in expected]
    for f, e in zip(fused, expected):
        assert f["rrf_score"] == pytest.approx(e["rrf_score"])


def test_rrf_weight_change_flips_ordering(monkeypatch) -> None:
    """向量 2.0 / BM25 0.5 时融合排序应随权重变化（与等权相反）。

    场景：向量路 A 第 1、B 第 2；BM25 路 B 第 1、A 第 5（k=60）。
    等权：B = 1/62 + 1/61 > A = 1/61 + 1/65，B 在前；
    向量 2.0 / BM25 0.5：A = 2/61 + 0.5/65 > B = 2/62 + 0.5/61，A 反超。
    """
    vector_results = [
        _doc("a", "文档 A 内容"),
        _doc("b", "文档 B 内容"),
    ]
    bm25_results = [
        _doc("b", "文档 B 内容"),
        _doc("x", "文档 X 内容"),
        _doc("y", "文档 Y 内容"),
        _doc("z", "文档 Z 内容"),
        _doc("a", "文档 A 内容"),
    ]

    monkeypatch.setattr(engine.settings, "RRF_VECTOR_WEIGHT", 1.0)
    monkeypatch.setattr(engine.settings, "RRF_BM25_WEIGHT", 1.0)
    fused_default = engine._rrf_fuse(vector_results, bm25_results, k=60)
    assert [d["doc_id"] for d in fused_default[:2]] == ["b", "a"]

    monkeypatch.setattr(engine.settings, "RRF_VECTOR_WEIGHT", 2.0)
    monkeypatch.setattr(engine.settings, "RRF_BM25_WEIGHT", 0.5)
    fused_weighted = engine._rrf_fuse(vector_results, bm25_results, k=60)
    assert [d["doc_id"] for d in fused_weighted[:2]] == ["a", "b"]
    # 权重公式生效：score = weight / (k + rank + 1)
    assert fused_weighted[0]["rrf_score"] == pytest.approx(2.0 / 61 + 0.5 / 65)
    assert fused_weighted[1]["rrf_score"] == pytest.approx(2.0 / 62 + 0.5 / 61)


def test_rrf_zero_weight_disables_vector_list(monkeypatch) -> None:
    """向量权重为 0 时该路不参与，结果等价于仅 BM25 单路。"""
    monkeypatch.setattr(engine.settings, "RRF_VECTOR_WEIGHT", 0.0)
    monkeypatch.setattr(engine.settings, "RRF_BM25_WEIGHT", 1.0)

    vector_results = [
        _doc("a", "文档 A 内容"),
        _doc("b", "文档 B 内容"),
    ]
    bm25_results = [
        _doc("c", "文档 C 内容"),
        _doc("a", "文档 A 内容"),
    ]

    fused = engine._rrf_fuse(vector_results, bm25_results, k=60)
    assert [d["doc_id"] for d in fused] == ["c", "a"]
    assert fused[0]["rrf_score"] == pytest.approx(1.0 / 61)
    assert fused[1]["rrf_score"] == pytest.approx(1.0 / 62)


def test_rrf_zero_weight_disables_bm25_list(monkeypatch) -> None:
    """BM25 权重为 0 时该路不参与，结果等价于仅向量单路。"""
    monkeypatch.setattr(engine.settings, "RRF_VECTOR_WEIGHT", 1.0)
    monkeypatch.setattr(engine.settings, "RRF_BM25_WEIGHT", 0.0)

    vector_results = [
        _doc("a", "文档 A 内容"),
        _doc("b", "文档 B 内容"),
    ]
    bm25_results = [
        _doc("b", "文档 B 内容"),  # BM25 排名更靠前也不参与
        _doc("c", "文档 C 内容"),
    ]

    fused = engine._rrf_fuse(vector_results, bm25_results, k=60)
    assert [d["doc_id"] for d in fused] == ["a", "b"]
    assert fused[0]["rrf_score"] == pytest.approx(1.0 / 61)
    assert fused[1]["rrf_score"] == pytest.approx(1.0 / 62)
