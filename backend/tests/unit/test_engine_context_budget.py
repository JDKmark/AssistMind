"""engine 检索预算与 CRAG 严格门单元测试（P1-1 / P1-3，离线）。

- _select_contexts：按 token 预算拼装，替代死取 top_k 条
- resolve_retrieval：改写重检索后复查质量，仍达不到 generate 线就拒答
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.core.rag import engine

settings = get_settings()


def _chunk(text: str, score: float = 0.0) -> dict:
    return {"text": text, "doc_id": f"d{score}", "rerank_score": score}


# ---------------------------------------------------------------- P1-1 上下文预算


def test_budget_stops_when_exhausted(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_TOKEN_BUDGET", 3500)
    monkeypatch.setattr(settings, "CONTEXT_MAX_COUNT", 16)
    ranked = [_chunk("字" * 500, score=i) for i in range(12)]
    out = engine._select_contexts(ranked, max_count=16)
    # 每条估 350 tokens（500×0.7），3500 预算装 10 条（3500 恰好），第 11 条超限
    assert len(out) == 10
    assert out[0]["rerank_score"] == 0 and out[-1]["rerank_score"] == 9  # 分数高的先进


def test_budget_disabled_falls_back_to_count(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_TOKEN_BUDGET", 0)
    monkeypatch.setattr(settings, "CONTEXT_MAX_COUNT", 16)
    ranked = [_chunk("字" * 500, score=i) for i in range(12)]
    assert len(engine._select_contexts(ranked, max_count=4)) == 4  # 退回按条数截断


def test_first_oversized_chunk_still_kept(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_TOKEN_BUDGET", 100)
    ranked = [_chunk("字" * 5000, score=0), _chunk("字" * 10, score=1)]
    out = engine._select_contexts(ranked, max_count=16)
    assert len(out) == 1 and out[0]["rerank_score"] == 0  # 最相关的一条永远进上下文


def test_max_count_hard_cap(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_TOKEN_BUDGET", 999999)
    monkeypatch.setattr(settings, "CONTEXT_MAX_COUNT", 3)
    ranked = [_chunk("短", score=i) for i in range(10)]
    assert len(engine._select_contexts(ranked, max_count=3)) == 3


# ---------------------------------------------------------------- P1-3 CRAG 严格门


def _retrieval(action: str, n_contexts: int = 2) -> dict:
    return {
        "contexts": [_chunk("内容", score=i) for i in range(n_contexts)],
        "crag": {"score": 0.5, "action": action, "degraded": False},
        "degraded": [],
        "rewrites": {"variants": ["变体问题"], "degraded": False},
    }


def test_strict_gate_rejects_weak_retry(monkeypatch):
    """改写重检索后仍是 rewrite_retry / no_result 级别 → 拒答（返回空 contexts）。"""
    monkeypatch.setattr(settings, "CRAG_RETRY_STRICT", True)

    async def fake_retrieve(query, role="user"):
        return _retrieval("rewrite_retry")  # 重检索后质量仍不达标

    monkeypatch.setattr(engine, "retrieve", fake_retrieve)
    first = _retrieval("rewrite_retry")
    contexts, crag, degraded = asyncio.run(
        engine.resolve_retrieval("问题", "user", first)
    )
    assert contexts == []
    assert crag["action"] == "rewrite_retry"


def test_strict_gate_off_keeps_old_behavior(monkeypatch):
    monkeypatch.setattr(settings, "CRAG_RETRY_STRICT", False)

    async def fake_retrieve(query, role="user"):
        return _retrieval("rewrite_retry")

    monkeypatch.setattr(engine, "retrieve", fake_retrieve)
    contexts, _, _ = asyncio.run(
        engine.resolve_retrieval("问题", "user", _retrieval("rewrite_retry"))
    )
    assert len(contexts) == 2  # 旧行为：重检索有结果就生成


def test_strict_gate_passes_good_retry(monkeypatch):
    monkeypatch.setattr(settings, "CRAG_RETRY_STRICT", True)

    async def fake_retrieve(query, role="user"):
        return _retrieval("generate")  # 重检索后达标

    monkeypatch.setattr(engine, "retrieve", fake_retrieve)
    contexts, _, _ = asyncio.run(
        engine.resolve_retrieval("问题", "user", _retrieval("rewrite_retry"))
    )
    assert len(contexts) == 2


def test_no_result_still_short_circuits(monkeypatch):
    monkeypatch.setattr(settings, "CRAG_RETRY_STRICT", True)
    contexts, crag, _ = asyncio.run(
        engine.resolve_retrieval("问题", "user", _retrieval("no_result", n_contexts=0))
    )
    assert contexts == [] and crag["action"] == "no_result"
