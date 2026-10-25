"""查询改写接入对话历史（消解指代）单元测试。

契约：
- rewrite(query, history)：Multi-Query/HyDE prompt 注入「对话上文」段，
  指示先消解指代再产出变体
- 不带 history：prompt 不含历史段，行为与原版完全一致（向后兼容）
- LLM 失败仍降级原问题（degraded=True），历史不影响降级语义
"""

from unittest.mock import AsyncMock, patch

from app.core import query_rewriter

_HISTORY = [
    {"role": "user", "content": "AssistMind 企业版多少钱"},
    {"role": "assistant", "content": "企业版按年订阅，具体报价见官网"},
]


async def test_multi_query_prompt_includes_history():
    """带 history：Multi-Query prompt 含历史段与消解指代指令。"""
    captured: dict = {}

    async def _capture(prompt, system=None, fast=False):
        captured["prompt"] = prompt
        return "企业版价格是多少\nAssistMind 企业版报价\n企业版一年多少钱"

    with patch.object(query_rewriter, "call_llm", new=_capture):
        result = await query_rewriter.rewrite("那它怎么买", history=_HISTORY)

    assert result["degraded"] is False
    assert "AssistMind 企业版多少钱" in captured["prompt"]
    assert "那它怎么买" in captured["prompt"]
    assert "对话上文" in captured["prompt"]
    assert "消解" in captured["prompt"] or "还原成具体对象" in captured["prompt"]


async def test_rewrite_without_history_prompt_unchanged():
    """向后兼容：不带 history 时 prompt 不含历史段。"""
    captured: dict = {}

    async def _capture(prompt, system=None, fast=False):
        captured["prompt"] = prompt
        return "某个变体表述"

    with patch.object(query_rewriter, "call_llm", new=_capture):
        result = await query_rewriter.rewrite("如何配置系统")

    assert "对话上文" not in captured["prompt"]
    assert result["all_queries"][0] == "如何配置系统"


async def test_hyde_prompt_includes_history():
    """HyDE 策略同样注入历史段。"""
    captured: dict = {}

    async def _capture(prompt, system=None, fast=False):
        captured["prompt"] = prompt
        return "假设答案：企业版支持多种购买方式"

    with patch.object(query_rewriter.settings, "QUERY_REWRITE_STRATEGY", "hyde"), patch.object(
        query_rewriter, "call_llm", new=_capture
    ):
        await query_rewriter.rewrite("那它怎么买", history=_HISTORY)

    assert "对话上文" in captured["prompt"]


async def test_history_does_not_change_degradation_semantics():
    """LLM 失败时降级原问题——带 history 与否降级语义一致。"""
    from app.core.infra.llm_factory import LLMUnavailableError

    with patch.object(
        query_rewriter,
        "call_llm",
        new=AsyncMock(side_effect=LLMUnavailableError("down")),
    ):
        result = await query_rewriter.rewrite("那它怎么买", history=_HISTORY)

    assert result["degraded"] is True
    assert result["variants"] == []
    assert result["all_queries"] == ["那它怎么买"]
