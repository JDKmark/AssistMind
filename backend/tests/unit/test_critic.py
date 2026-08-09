"""CRAG 评估器单元测试。

覆盖：
- 空上下文返回 no_result
- 正则匹配整数（修复前的 bug：r"([0-9]*\\.?[0-9]+)" 不能匹配 "1"）
- 正则匹配小数
- LLM 不可用时降级走 generate
- 三种 action 阈值判断
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.core.infra.llm_factory import LLMUnavailableError
from app.core.rag.critic import _SCORE_PATTERN, evaluate


def test_score_pattern_matches_integer():
    """正则应能匹配整数 "1"（P1 修复点）。"""
    match = _SCORE_PATTERN.search("1")
    assert match is not None
    assert match.group(1) == "1"


def test_score_pattern_matches_decimal():
    """正则应能匹配小数 "0.85"。"""
    match = _SCORE_PATTERN.search("0.85")
    assert match is not None
    assert match.group(1) == "0.85"


def test_score_pattern_matches_integer_in_text():
    """正则应能从文本中提取整数。"""
    match = _SCORE_PATTERN.search("相关性得分：1")
    assert match is not None
    assert match.group(1) == "1"


def test_score_pattern_matches_zero():
    """正则应能匹配 "0"。"""
    match = _SCORE_PATTERN.search("0")
    assert match is not None
    assert match.group(1) == "0"


async def test_evaluate_empty_contexts_returns_no_result():
    """空上下文应返回 no_result。"""
    result = await evaluate("问题", [])
    assert result["action"] == "no_result"
    assert result["score"] == 0.0


async def test_evaluate_high_score_returns_generate():
    """高分应返回 generate。"""
    contexts = [{"text": "相关内容"}]
    with patch("app.core.rag.critic.call_llm", new=AsyncMock(return_value="0.9")):
        result = await evaluate("问题", contexts)
    assert result["action"] == "generate"
    assert result["score"] == 0.9
    assert not result["degraded"]


async def test_evaluate_medium_score_returns_rewrite_retry():
    """中等分应返回 rewrite_retry。"""
    contexts = [{"text": "部分相关内容"}]
    with patch("app.core.rag.critic.call_llm", new=AsyncMock(return_value="0.5")):
        result = await evaluate("问题", contexts)
    assert result["action"] == "rewrite_retry"


async def test_evaluate_low_score_returns_no_result():
    """低分应返回 no_result。"""
    contexts = [{"text": "不相关内容"}]
    with patch("app.core.rag.critic.call_llm", new=AsyncMock(return_value="0.1")):
        result = await evaluate("问题", contexts)
    assert result["action"] == "no_result"


async def test_evaluate_integer_score():
    """LLM 返回整数 "1" 应正确解析（P1 修复点）。"""
    contexts = [{"text": "完全相关"}]
    with patch("app.core.rag.critic.call_llm", new=AsyncMock(return_value="1")):
        result = await evaluate("问题", contexts)
    assert result["score"] == 1.0
    assert result["action"] == "generate"


async def test_evaluate_llm_unavailable_degrades():
    """LLM 不可用时应降级走 generate 路径。"""
    contexts = [{"text": "内容"}]
    with patch(
        "app.core.rag.critic.call_llm",
        new=AsyncMock(side_effect=LLMUnavailableError("all down")),
    ):
        result = await evaluate("问题", contexts)
    assert result["action"] == "generate"
    assert result["degraded"] is True
    assert result["score"] == 1.0


async def test_evaluate_exception_degrades():
    """其他异常也应降级。"""
    contexts = [{"text": "内容"}]
    with patch(
        "app.core.rag.critic.call_llm",
        new=AsyncMock(side_effect=RuntimeError("意外错误")),
    ):
        result = await evaluate("问题", contexts)
    assert result["action"] == "generate"
    assert result["degraded"] is True


async def test_evaluate_score_clamped_to_range():
    """分数应被限制在 [0, 1] 范围内。"""
    contexts = [{"text": "内容"}]
    with patch("app.core.rag.critic.call_llm", new=AsyncMock(return_value="1.5")):
        result = await evaluate("问题", contexts)
    assert result["score"] == 1.0
