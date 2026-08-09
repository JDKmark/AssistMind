"""意图路由单元测试。

覆盖：
- 规则命中（task / faq）
- 语义命中
- 语义未命中（相似度低）返回 None
- LLM 兜底
- embedding 失败降级
- LLM 失败降级
- 低置信度判定
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.infra.llm_factory import LLMUnavailableError
from app.core.router import confidence, intent, semantic


@pytest.fixture(autouse=True)
def _reset_router_caches():
    """每个测试前后重置路由模块级缓存，保证隔离。"""
    semantic.reset_cache()
    intent._routes_cache["mtime"] = None
    intent._routes_cache["data"] = None
    yield
    semantic.reset_cache()
    intent._routes_cache["mtime"] = None
    intent._routes_cache["data"] = None


def _orthogonal_sample_vecs():
    """构造与 query 正交的样本向量列表（用于语义未命中场景）。"""
    routes = intent._load_routes()
    samples = semantic._collect_samples(routes)
    return [[0.0, 1.0, 0.0] for _ in samples]


async def test_rule_match_task():
    """规则命中 task：query 含"创建工单"，应短路返回 rule。"""
    with patch("app.core.router.semantic.embed_one", new=AsyncMock(return_value=None)), patch(
        "app.core.router.intent.call_llm",
        new=AsyncMock(side_effect=AssertionError("规则命中不应调用 LLM")),
    ):
        result = await intent.route("创建工单")

    assert result["intent"] == "task"
    assert result["confidence"] == 1.0
    assert result["source"] == "rule"
    assert result["low_confidence"] is False


async def test_rule_match_faq():
    """规则命中 faq：query 含"如何配置"。"""
    with patch("app.core.router.semantic.embed_one", new=AsyncMock(return_value=None)), patch(
        "app.core.router.intent.call_llm",
        new=AsyncMock(side_effect=AssertionError("规则命中不应调用 LLM")),
    ):
        result = await intent.route("如何配置")

    assert result["intent"] == "faq"
    assert result["confidence"] == 1.0
    assert result["source"] == "rule"


async def test_semantic_hit():
    """语义命中：首个样本与 query 完全相同向量，其余正交，应命中 faq。"""
    routes = intent._load_routes()
    samples = semantic._collect_samples(routes)
    q_vec = [1.0, 0.0, 0.0]
    sample_vecs = []
    for i in range(len(samples)):
        sample_vecs.append([1.0, 0.0, 0.0] if i == 0 else [0.0, 1.0, 0.0])

    with patch("app.core.router.semantic.embed_one", new=AsyncMock(return_value=q_vec)):
        with patch("app.core.router.semantic.embed_async", new=AsyncMock(return_value=sample_vecs)):
            result = await semantic.semantic_route("AssistMind 是什么")

    assert result is not None
    assert result["source"] == "semantic"
    assert result["intent"] == "faq"
    assert result["confidence"] == pytest.approx(1.0)
    assert result["low_confidence"] is False


async def test_semantic_miss_returns_none():
    """语义未命中：所有样本与 query 正交，最高相似度 0 < 阈值，返回 None。"""
    q_vec = [1.0, 0.0, 0.0]
    sample_vecs = _orthogonal_sample_vecs()

    with patch("app.core.router.semantic.embed_one", new=AsyncMock(return_value=q_vec)):
        with patch("app.core.router.semantic.embed_async", new=AsyncMock(return_value=sample_vecs)):
            result = await semantic.semantic_route("随便说说")

    assert result is None


async def test_llm_fallback_when_semantic_miss():
    """LLM 兜底：语义未命中后进入 LLM 层，返回 task。"""
    q_vec = [1.0, 0.0, 0.0]
    sample_vecs = _orthogonal_sample_vecs()

    with patch("app.core.router.semantic.embed_one", new=AsyncMock(return_value=q_vec)):
        with patch("app.core.router.semantic.embed_async", new=AsyncMock(return_value=sample_vecs)):
            with patch("app.core.router.intent.call_llm", new=AsyncMock(return_value="task")):
                result = await intent.route("帮我处理一下这个事情")

    assert result["intent"] == "task"
    assert result["source"] == "llm"
    # 裸文本解析默认置信度 0.7
    assert result["confidence"] == pytest.approx(0.7)
    assert result["low_confidence"] is False


async def test_embedding_fail_falls_to_llm():
    """embedding 失败（embed_one 返回 None）应走 LLM。"""
    with patch("app.core.router.semantic.embed_one", new=AsyncMock(return_value=None)):
        with patch("app.core.router.intent.call_llm", new=AsyncMock(return_value="chat")):
            result = await intent.route("某个不匹配规则的问题")

    assert result["source"] == "llm"
    assert result["intent"] == "chat"


async def test_llm_unavailable_fallback():
    """LLM 不可用时应降级 unclear，source=fallback。"""
    q_vec = [1.0, 0.0, 0.0]
    sample_vecs = _orthogonal_sample_vecs()

    with patch("app.core.router.semantic.embed_one", new=AsyncMock(return_value=q_vec)):
        with patch("app.core.router.semantic.embed_async", new=AsyncMock(return_value=sample_vecs)):
            with patch(
                "app.core.router.intent.call_llm",
                new=AsyncMock(side_effect=LLMUnavailableError("all down")),
            ):
                result = await intent.route("一段完全无关的文字")

    assert result["intent"] == "unclear"
    assert result["source"] == "fallback"
    assert result["confidence"] == 0.0
    assert result["low_confidence"] is True


def test_low_confidence_flags():
    """低置信度判定：语义/LLM 得分低于阈值时 low_confidence=True；规则始终高置信。"""
    # 语义相似度低于 0.6
    low = confidence.compute("semantic", 0.5, threshold=0.6)
    assert low["confidence"] == pytest.approx(0.5)
    assert low["low_confidence"] is True

    # 语义相似度高于阈值
    high = confidence.compute("semantic", 0.9, threshold=0.6)
    assert high["low_confidence"] is False

    # LLM 低置信
    llm_low = confidence.compute("llm", 0.4, threshold=0.6)
    assert llm_low["low_confidence"] is True

    # 规则始终 confidence=1.0，高置信
    rule = confidence.compute("rule", 0.0)
    assert rule["confidence"] == 1.0
    assert rule["low_confidence"] is False
