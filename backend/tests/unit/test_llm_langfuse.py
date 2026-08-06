"""LLM 工厂 Langfuse 埋点单元测试。

覆盖：
- 启用时 call_llm 成功 → 产生一个 llm.call span，metadata 含 provider/model/status=ok
- 启用时全部 provider 失败 → span 标记 level=ERROR，异常原样抛出
- 未启用 → 零开销：不调 get_langfuse、不创建 span，返回值正常
- 降级场景 metadata（provider=ollama、fallback=True）与 span 创建失败不阻塞

mock 策略：
- llm_factory 在模块顶部 `from app.core.infra.langfuse import get_langfuse,
  is_langfuse_enabled` 已绑定引用，因此 patch 目标是
  app.core.infra.llm_factory.is_langfuse_enabled / .get_langfuse，
  而不是 langfuse 模块本身（否则不生效）
- LLM 降级链路用 AsyncMock patch _deepseek_with_retry / _ollama_with_retry
  （与 test_llm_factory.py 一致），不连真实网络
- 假客户端记录 start_observation 调用，假 span 记录 update/end
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.infra import llm_factory as llm_factory_module
from app.core.infra.llm_factory import LLMUnavailableError, call_llm


class _FakeSpan:
    """记录 update/end 调用的假 span。"""

    def __init__(self, name: str):
        self.name = name
        self.updates: list[dict] = []
        self.ended = False

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)

    def end(self) -> None:
        self.ended = True


class _FakeLangfuseClient:
    """记录 start_observation 调用的假 Langfuse 客户端。"""

    def __init__(self):
        self.spans: list[_FakeSpan] = []
        self.observations: list[tuple[str, dict]] = []

    def start_observation(self, name: str, **kwargs) -> _FakeSpan:
        span = _FakeSpan(name)
        self.spans.append(span)
        self.observations.append((name, kwargs))
        return span


@pytest.fixture
def fake_langfuse():
    """启用 Langfuse：patch llm_factory 内的引用，返回假客户端。"""
    client = _FakeLangfuseClient()
    with patch.object(llm_factory_module, "is_langfuse_enabled", return_value=True):
        with patch.object(llm_factory_module, "get_langfuse", return_value=client):
            yield client


async def test_call_llm_langfuse_span_success(fake_langfuse, mock_llm_success):
    """启用时成功调用产生一个 llm.call span，metadata 含 provider/model/status=ok。"""
    result = await call_llm("你好", system="你是助手")
    assert result == "LLM 响应"

    # 恰好一个 span，名称 llm.call，类型 span
    assert len(fake_langfuse.spans) == 1
    span = fake_langfuse.spans[0]
    assert span.name == "llm.call"
    _, kwargs = fake_langfuse.observations[0]
    assert kwargs["as_type"] == "span"
    assert kwargs["input"]["prompt"] == "你好"
    assert kwargs["input"]["system"] == "你是助手"

    # 成功路径：一次 update（output + metadata），随后 end
    assert len(span.updates) == 1
    upd = span.updates[0]
    assert upd["output"] == "LLM 响应"
    meta = upd["metadata"]
    assert meta["provider"] == "deepseek"
    assert meta["status"] == "ok"
    assert meta["model"]  # 非空
    assert meta["retry_count"] == 0
    assert meta["fallback"] is False
    assert isinstance(meta["duration_ms"], int) and meta["duration_ms"] >= 0
    assert meta["content_length"] == len("LLM 响应")
    assert span.ended


async def test_call_llm_langfuse_error_marks_span(fake_langfuse):
    """全部 provider 失败时 span 标记 level=ERROR，异常原样抛出。"""
    with patch(
        "app.core.infra.llm_factory._deepseek_with_retry",
        new=AsyncMock(side_effect=Exception("DeepSeek 挂了")),
    ):
        with patch(
            "app.core.infra.llm_factory._ollama_with_retry",
            new=AsyncMock(side_effect=Exception("Ollama 挂了")),
        ):
            with pytest.raises(LLMUnavailableError):
                await call_llm("你好")

    assert len(fake_langfuse.spans) == 1
    span = fake_langfuse.spans[0]
    assert span.ended
    assert len(span.updates) == 1
    upd = span.updates[0]
    assert upd["level"] == "ERROR"
    assert "LLMUnavailableError" in upd["status_message"]
    assert upd["metadata"]["status"] == "error"
    assert upd["metadata"]["fallback"] is True


async def test_call_llm_langfuse_disabled_noop(mock_llm_success):
    """未启用时零开销：不调 get_langfuse、不创建 span，返回值正常。"""
    mock_get = MagicMock()
    with patch.object(llm_factory_module, "is_langfuse_enabled", return_value=False):
        with patch.object(llm_factory_module, "get_langfuse", mock_get):
            result = await call_llm("你好")
    assert result == "LLM 响应"
    mock_get.assert_not_called()


async def test_call_llm_langfuse_fallback_metadata(fake_langfuse):
    """DeepSeek 失败切 Ollama 成功时 metadata 记录 provider=ollama 与 fallback=True。"""
    with patch(
        "app.core.infra.llm_factory._deepseek_with_retry",
        new=AsyncMock(side_effect=Exception("DeepSeek 挂了")),
    ):
        with patch(
            "app.core.infra.llm_factory._ollama_with_retry",
            new=AsyncMock(return_value="Ollama 兜底响应"),
        ):
            result = await call_llm("你好")
    assert result == "Ollama 兜底响应"
    meta = fake_langfuse.spans[0].updates[0]["metadata"]
    assert meta["provider"] == "ollama"
    assert meta["fallback"] is True
    assert meta["status"] == "ok"


async def test_call_llm_langfuse_span_create_failure_does_not_block(mock_llm_success):
    """span 创建失败（客户端异常）时降级为不埋点，调用不受影响。"""

    class _BrokenClient:
        def start_observation(self, **kwargs):
            raise RuntimeError("langfuse 挂了")

    with patch.object(llm_factory_module, "is_langfuse_enabled", return_value=True):
        with patch.object(llm_factory_module, "get_langfuse", return_value=_BrokenClient()):
            result = await call_llm("你好")
    assert result == "LLM 响应"
