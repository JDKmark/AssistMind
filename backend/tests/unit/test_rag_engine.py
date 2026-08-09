"""RAGEngine 单元测试。

覆盖：
- retrieve 全链路：改写 + embedding + 并行召回 + RRF + Reranker + CRAG
- embedding 降级：仅 BM25
- qdrant 降级：仅 BM25
- 两路都失败：no_result
- reranker 失败：用 RRF 结果
- CRAG no_result 重检查（P1 修复点）
- LLM 不可用时模板兜底
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.core.infra.llm_factory import LLMUnavailableError
from app.core.rag import engine


async def test_retrieve_full_chain(mock_embedding, mock_qdrant, bm25_with_docs, mock_reranker):
    """全链路检索应返回 contexts 和 CRAG 评估。"""
    with patch("app.core.rag.engine.rewrite_query", new=AsyncMock(return_value={
        "original": "AssistMind 是什么",
        "variants": ["AssistMind 介绍"],
        "hyde": None,
        "all_queries": ["AssistMind 是什么", "AssistMind 介绍"],
        "degraded": False,
    })), patch("app.core.rag.engine.crag_evaluate", new=AsyncMock(return_value={
        "score": 0.9, "action": "generate", "degraded": False,
    })):
        result = await engine.retrieve("AssistMind 是什么")

    assert len(result["contexts"]) > 0
    assert result["crag"]["action"] == "generate"
    assert result["degraded"] == []


async def test_retrieve_embedding_degrades_to_bm25_only(
    mock_embedding_fail, mock_qdrant, bm25_with_docs, mock_reranker
):
    """embedding 失败应降级为仅 BM25 召回。"""
    with patch("app.core.rag.engine.rewrite_query", new=AsyncMock(return_value={
        "original": "AssistMind",
        "variants": [],
        "hyde": None,
        "all_queries": ["AssistMind"],
        "degraded": False,
    })), patch("app.core.rag.engine.crag_evaluate", new=AsyncMock(return_value={
        "score": 0.8, "action": "generate", "degraded": False,
    })):
        result = await engine.retrieve("AssistMind")

    assert "embedding" in result["degraded"]
    assert "qdrant" in result["degraded"]  # embedding 失败导致 vector_results 为空
    assert len(result["contexts"]) > 0  # BM25 仍返回结果


async def test_retrieve_both_fail_returns_no_result(
    mock_embedding_fail, mock_qdrant_fail, bm25_with_docs
):
    """两路召回都失败应返回 no_result。"""
    # 让 BM25 也返回空
    bm25_with_docs._docs = []
    bm25_with_docs._bm25 = None

    with patch("app.core.rag.engine.rewrite_query", new=AsyncMock(return_value={
        "original": "不存在的",
        "variants": [],
        "hyde": None,
        "all_queries": ["不存在的"],
        "degraded": False,
    })):
        result = await engine.retrieve("不存在的")

    assert result["contexts"] == []
    assert result["crag"]["action"] == "no_result"


async def test_retrieve_reranker_fail_uses_rrf(
    mock_embedding, mock_qdrant, bm25_with_docs, mock_reranker_fail
):
    """reranker 失败应用 RRF 结果。"""
    with patch("app.core.rag.engine.rewrite_query", new=AsyncMock(return_value={
        "original": "AssistMind",
        "variants": [],
        "hyde": None,
        "all_queries": ["AssistMind"],
        "degraded": False,
    })), patch("app.core.rag.engine.crag_evaluate", new=AsyncMock(return_value={
        "score": 0.8, "action": "generate", "degraded": False,
    })):
        result = await engine.retrieve("AssistMind")

    assert "reranker" in result["degraded"]
    assert len(result["contexts"]) > 0


async def test_answer_no_result_returns_fallback():
    """CRAG 判定 no_result 时应返回兜底响应。"""
    with patch("app.core.rag.engine.retrieve", new=AsyncMock(return_value={
        "query": "问题",
        "rewrites": {"original": "问题", "variants": [], "hyde": None, "all_queries": ["问题"], "degraded": False},
        "contexts": [],
        "crag": {"score": 0.0, "action": "no_result", "degraded": False},
        "degraded": [],
    })):
        result = await engine.answer("问题")

    assert "未找到" in result["answer"]
    assert result["sources"] == []


async def test_answer_rewrite_retry_no_result_after_retry():
    """P1 修复：rewrite_retry 重检索后仍 no_result 应返回兜底（不能返回错误答案）。"""
    # 第一次 retrieve 返回 rewrite_retry
    first_retrieval = {
        "query": "问题",
        "rewrites": {"original": "问题", "variants": ["变体1"], "hyde": None, "all_queries": ["问题", "变体1"], "degraded": False},
        "contexts": [{"text": "部分相关"}],
        "crag": {"score": 0.5, "action": "rewrite_retry", "degraded": False},
        "degraded": [],
    }
    # 第二次 retrieve（重检索）返回 no_result
    second_retrieval = {
        "query": "变体1",
        "rewrites": {"original": "变体1", "variants": [], "hyde": None, "all_queries": ["变体1"], "degraded": False},
        "contexts": [],
        "crag": {"score": 0.0, "action": "no_result", "degraded": False},
        "degraded": [],
    }

    call_count = [0]
    async def _mock_retrieve(query, role="user", top_k=8):
        call_count[0] += 1
        return first_retrieval if call_count[0] == 1 else second_retrieval

    with patch("app.core.rag.engine.retrieve", new=_mock_retrieve):
        result = await engine.answer("问题")

    assert "未找到" in result["answer"]
    assert result["sources"] == []


async def test_answer_llm_unavailable_uses_template():
    """LLM 不可用时应模板兜底。"""
    with patch("app.core.rag.engine.retrieve", new=AsyncMock(return_value={
        "query": "问题",
        "rewrites": {"original": "问题", "variants": [], "hyde": None, "all_queries": ["问题"], "degraded": False},
        "contexts": [{"text": "相关内容", "doc_id": "d1", "title": "t", "source": "s"}],
        "crag": {"score": 0.9, "action": "generate", "degraded": False},
        "degraded": [],
    })):
        with patch("app.core.rag.engine.call_llm", new=AsyncMock(side_effect=LLMUnavailableError("all down"))):
            result = await engine.answer("问题")

    assert "服务暂时繁忙" in result["answer"]
    assert "llm" in result["degraded"]


async def test_answer_generate_success():
    """正常生成路径。"""
    with patch("app.core.rag.engine.retrieve", new=AsyncMock(return_value={
        "query": "问题",
        "rewrites": {"original": "问题", "variants": [], "hyde": None, "all_queries": ["问题"], "degraded": False},
        "contexts": [{"text": "内容", "doc_id": "d1", "title": "t", "source": "s"}],
        "crag": {"score": 0.9, "action": "generate", "degraded": False},
        "degraded": [],
    })):
        with patch("app.core.rag.engine.call_llm", new=AsyncMock(return_value="生成的答案")):
            result = await engine.answer("问题")

    assert result["answer"] == "生成的答案"
    assert len(result["sources"]) == 1
    assert result["degraded"] == []
