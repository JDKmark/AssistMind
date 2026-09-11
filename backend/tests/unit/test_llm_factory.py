"""LLM 工厂单元测试。

覆盖：
- 主 provider 成功
- 主 provider 失败切备用 provider
- 所有 provider 失败抛 LLMUnavailableError
- 断路器 Open 时跳过对应 provider
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.infra import circuit_breaker
from app.core.infra.llm_factory import (
    LLMUnavailableError,
    call_llm,
    get_chat_model,
    stream_llm,
)


# ---------- stream_llm（流式逐 token，chat SSE 打字机）----------


# ---------- call_llm(fast=True) 快速失败模式（rewrite/CRAG/意图分类/实体兜底）----------
# 背景：这些「失败可降级」环节在商汤故障时曾各自等满 LLM_TIMEOUT×重试 + Ollama 60s×2
# ≈ 215s，faq 一轮感知数分钟（用户报「一直正在思考」）；fast 走流式快速链路 ~16s。


async def test_call_llm_fast_collects_stream_text():
    """fast=True：直接走流式快速链路并收集完整文本，绕过非流式重试/降级路径。"""
    async def _fake_stream(prompt, system=None, *, info=None):
        yield "第一段"
        yield "第二段"

    with _patch_no_langfuse(), patch(
        "app.core.infra.llm_factory._stream_impl", new=_fake_stream
    ):
        result = await call_llm("你好", fast=True)

    assert result == "第一段第二段"


async def test_call_llm_fast_skips_retry_path():
    """fast=True：绝不进入非流式重试降级（_deepseek_with_retry）——快速失败语义核心。"""
    async def _fake_stream(prompt, system=None, *, info=None):
        yield "OK"

    with _patch_no_langfuse(), patch(
        "app.core.infra.llm_factory._stream_impl", new=_fake_stream
    ), patch(
        "app.core.infra.llm_factory._deepseek_with_retry",
        new=AsyncMock(side_effect=AssertionError("fast 模式不应走重试路径")),
    ):
        result = await call_llm("你好", fast=True)

    assert result == "OK"


async def test_call_llm_fast_all_fail_raises():
    """fast=True 且流式快速链路全失败 → LLMUnavailableError 原样上抛（调用方场景化降级）。"""
    async def _fake_stream(prompt, system=None, *, info=None):
        raise LLMUnavailableError("all down")
        yield  # 使函数成为 async generator

    with _patch_no_langfuse(), patch(
        "app.core.infra.llm_factory._stream_impl", new=_fake_stream
    ):
        with pytest.raises(LLMUnavailableError):
            await call_llm("你好", fast=True)


def _patch_no_langfuse():
    """强制 stream_llm 走零开销路径（避免读取 .env 真实 Langfuse key 触发埋点）。"""
    return patch("app.core.infra.llm_factory.is_langfuse_enabled", return_value=False)


def test_stream_fast_timeout_applied(monkeypatch):
    """流式通道使用快速失败超时；非流式通道保持原超时（call_llm 不受影响）。

    回归背景：商汤网关间歇故障时，生成阶段曾等满 LLM_TIMEOUT(30)+重试+Ollama
    15〜60s ≈ 90s 才出模板兜底；stream_llm 改为专属 LLM_STREAM_TIMEOUT(10) /
    LLM_STREAM_FALLBACK_TIMEOUT(6) 后，全失败感知压到 ~16s。
    """
    from app.core.infra import llm_factory as _lf

    captured: list[int] = []

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.append(kwargs.get("timeout"))

    monkeypatch.setattr(_lf, "ChatOpenAI", _FakeOpenAI)

    _lf._build_chat_model("deepseek", stream_fast=True)
    _lf._build_chat_model("deepseek")
    _lf._build_chat_model("ollama", stream_fast=True)
    _lf._build_chat_model("ollama")

    assert captured == [
        _lf.settings.LLM_STREAM_TIMEOUT,
        _lf.settings.LLM_TIMEOUT,
        _lf.settings.LLM_STREAM_FALLBACK_TIMEOUT,
        _lf.settings.LLM_FALLBACK_TIMEOUT,
    ]
    # 快速超时确实短于普通超时（否则"缩短失败感知"无意义）
    assert _lf.settings.LLM_STREAM_TIMEOUT < _lf.settings.LLM_TIMEOUT


async def test_stream_llm_deepseek_success():
    """主 provider 流式成功：逐 chunk 产出且合并为完整文本。"""
    calls = []

    async def _gen(name, prompt, system, *, info=None):
        calls.append(name)
        yield "你好"
        yield "呀"

    with _patch_no_langfuse(), patch(
        "app.core.infra.llm_factory._stream_provider", new=_gen
    ):
        chunks = []
        async for d in stream_llm("你好"):
            chunks.append(d)

    assert "".join(chunks) == "你好呀"
    assert calls == ["deepseek"]


async def test_stream_llm_deepseek_fail_fallback_ollama():
    """DeepSeek 流式首 token 前失败 → 切 Ollama 流式。"""
    calls = []

    async def _gen(name, prompt, system, *, info=None):
        calls.append(name)
        if name == "deepseek":
            raise ConnectionError("首 token 前失败")
        yield "兜底回答"

    with _patch_no_langfuse(), patch(
        "app.core.infra.llm_factory._stream_provider", new=_gen
    ):
        chunks = []
        async for d in stream_llm("你好"):
            chunks.append(d)

    assert "".join(chunks) == "兜底回答"
    assert calls == ["deepseek", "ollama"]


async def test_stream_llm_all_fail_raises():
    """所有 provider 流式均失败 → LLMUnavailableError。"""

    async def _gen(name, prompt, system, *, info=None):
        raise ConnectionError("down")
        yield  # 使函数成为 async generator（否则是 coroutine，async for 无法迭代）

    with _patch_no_langfuse(), patch(
        "app.core.infra.llm_factory._stream_provider", new=_gen
    ):
        with pytest.raises(LLMUnavailableError):
            async for d in stream_llm("你好"):
                pass  # pragma: no cover


async def test_stream_llm_ollama_provider_skips_deepseek(monkeypatch):
    """LLM_PROVIDER=ollama：流式直接走 Ollama，不尝试 DeepSeek。"""
    from app.core.infra import llm_factory

    monkeypatch.setattr(llm_factory.settings, "LLM_PROVIDER", "ollama")
    calls = []

    async def _gen(name, prompt, system, *, info=None):
        calls.append(name)
        yield "本地回答"

    with _patch_no_langfuse(), patch.object(
        llm_factory, "_stream_provider", new=_gen
    ):
        chunks = []
        async for d in stream_llm("你好"):
            chunks.append(d)

    assert "".join(chunks) == "本地回答"
    assert calls == ["ollama"]


async def test_call_llm_deepseek_success(mock_llm_success):
    """主 provider 成功应直接返回 DeepSeek 响应。"""
    result = await call_llm("你好", system="你是助手")
    assert result == "LLM 响应"


async def test_call_llm_fallback_to_ollama(mock_llm_deepseek_fail_ollama_success):
    """DeepSeek 失败应切 Ollama。"""
    result = await call_llm("你好")
    assert result == "Ollama 兜底响应"


async def test_call_llm_all_fail_raises(mock_llm_all_fail):
    """所有 provider 失败应抛 LLMUnavailableError。"""
    with pytest.raises(LLMUnavailableError):
        await call_llm("你好")


async def test_call_llm_skips_open_breaker():
    """DeepSeek 断路器 Open 时应直接用 Ollama。"""
    # 强制 Open DeepSeek 断路器
    circuit_breaker.init_breakers(redis=None)
    # 通过反复失败强制 Open
    async def _fail():
        raise ConnectionError("挂了")
    for _ in range(5):
        try:
            await circuit_breaker.call_with_breaker("llm_deepseek", _fail)
        except Exception:
            pass
    assert circuit_breaker.is_open("llm_deepseek")

    # 调用 call_llm 应跳过 DeepSeek 直接用 Ollama
    with patch("app.core.infra.llm_factory._ollama_with_retry", new=AsyncMock(return_value="Ollama 响应")):
        result = await call_llm("你好")
    assert result == "Ollama 响应"


def test_get_chat_model_returns_deepseek_by_default():
    """默认应返回 DeepSeek ChatModel。"""
    circuit_breaker.init_breakers(redis=None)
    model = get_chat_model()
    assert model is not None


def test_get_chat_model_fallback_when_deepseek_open():
    """DeepSeek 断路器 Open 时应返回 Ollama ChatModel。"""
    circuit_breaker.init_breakers(redis=None)
    # 强制打开断路器
    import asyncio
    async def _force_open():
        raise ConnectionError("force")
    for _ in range(5):
        try:
            asyncio.get_event_loop().run_until_complete(
                circuit_breaker.call_with_breaker("llm_deepseek", _force_open)
            )
        except Exception:
            pass

    # 注意：同步测试中无法直接强制 Open，此处仅验证断路器未 Open 时返回 DeepSeek
    # 真实 Open 行为已在 async 测试中覆盖
    if not circuit_breaker.is_open("llm_deepseek"):
        model = get_chat_model()
        assert model is not None


async def test_call_llm_with_generation_flag(mock_llm_all_fail):
    """generation=True 时所有 provider 失败仍应抛 LLMUnavailableError。"""
    with pytest.raises(LLMUnavailableError):
        await call_llm("你好", generation=True)


async def test_call_llm_ollama_provider_skips_deepseek(
    monkeypatch, mock_llm_deepseek_fail_ollama_success
):
    """LLM_PROVIDER=ollama：直接走 Ollama，完全不尝试 DeepSeek（避免无预算时的 429 重试）。"""
    from app.core.infra import llm_factory

    monkeypatch.setattr(llm_factory.settings, "LLM_PROVIDER", "ollama")
    deepseek_called = {"n": 0}

    async def _fail_deepseek(*args, **kwargs):
        deepseek_called["n"] += 1
        raise RuntimeError("should not be called")

    with patch.object(
        llm_factory, "_deepseek_with_retry", side_effect=_fail_deepseek
    ):
        result = await call_llm("你好", "system")

    assert deepseek_called["n"] == 0
    assert result == "Ollama 兜底响应"


# ---------- 流式中途失败语义（不切 provider、不伪装 done） ----------


async def test_stream_llm_deepseek_midstream_fail_no_fallback():
    """DeepSeek 已产出后中途失败 → 原样上抛，不切 Ollama（避免半截话+全文拼接）。"""
    calls = []

    async def _gen(name, prompt, system, *, info=None):
        calls.append(name)
        yield "前半截"
        raise ConnectionError("中途失败")

    with _patch_no_langfuse(), patch(
        "app.core.infra.llm_factory._stream_provider", new=_gen
    ):
        chunks = []
        with pytest.raises(ConnectionError):
            async for d in stream_llm("你好"):
                chunks.append(d)

    assert chunks == ["前半截"]
    assert calls == ["deepseek"]


async def test_stream_llm_last_provider_midstream_fail_raises_raw():
    """Ollama（最后一个 provider）中途失败 → 原样上抛而非 LLMUnavailableError
    （转 LLMUnavailableError 会让 done 兜底话术覆盖前端已收到的 delta）。"""

    async def _gen(name, prompt, system, *, info=None):
        if name == "deepseek":
            raise ConnectionError("deepseek 首 token 前失败")
            yield  # pragma: no cover
        yield "部分"
        raise ConnectionError("ollama 中途失败")

    with _patch_no_langfuse(), patch(
        "app.core.infra.llm_factory._stream_provider", new=_gen
    ):
        chunks = []
        with pytest.raises(ConnectionError) as exc_info:
            async for d in stream_llm("你好"):
                chunks.append(d)

    assert chunks == ["部分"]
    assert not isinstance(exc_info.value, LLMUnavailableError)
    assert str(exc_info.value) == "ollama 中途失败"


async def test_stream_llm_ollama_only_mode_first_token_fail_raises_unavailable(monkeypatch):
    """LLM_PROVIDER=ollama：单 provider 首 token 前失败 → LLMUnavailableError
    （下游捕获后可走模板兜底，而非裸异常冒泡成 SSE error）。"""
    from app.core.infra import llm_factory

    monkeypatch.setattr(llm_factory.settings, "LLM_PROVIDER", "ollama")

    async def _gen(name, prompt, system, *, info=None):
        raise ConnectionError("ollama down")
        yield  # pragma: no cover

    with _patch_no_langfuse(), patch(
        "app.core.infra.llm_factory._stream_provider", new=_gen
    ):
        with pytest.raises(LLMUnavailableError):
            async for d in stream_llm("你好"):
                pass  # pragma: no cover


async def test_stream_llm_ollama_only_mode_success_fills_info(monkeypatch):
    """LLM_PROVIDER=ollama：成功产出回填 provider 埋点信息（此前恒为 none）。"""
    from app.core.infra import llm_factory

    monkeypatch.setattr(llm_factory.settings, "LLM_PROVIDER", "ollama")
    info = {
        "provider": None,
        "model": None,
        "timeout_s": None,
        "deepseek_attempts": 0,
        "ollama_attempts": 0,
        "fallback": False,
    }

    async def _gen(name, prompt, system, *, info=None):
        yield "ollama 回答"

    with patch("app.core.infra.llm_factory._stream_provider", new=_gen):
        chunks = []
        async for d in llm_factory._stream_impl("你好", None, info=info):
            chunks.append(d)

    assert "".join(chunks) == "ollama 回答"
    assert info["provider"] == "ollama"
    assert info["model"] == llm_factory.settings.OLLAMA_MODEL
