"""工具调用 span 埋点（tracing.py）单元测试。

覆盖：
- 未启用 → new_tool_span 返回 None（零开销），finish_tool_span 立即返回
- 启用 → span 名 `tool.<工具名>`、input 为入参；结束时写 output / elapsed_ms / ok
- 工具异常 → span 记 level=ERROR 与 status_message
- 结果过大 → 截断为字符串，避免单条 span 撑大上报体积
- span 创建失败 → 返回 None，不抛异常（工具照常执行）

mock 策略：tracing 在模块顶部 `from app.core.infra.langfuse import
get_langfuse, is_langfuse_enabled` 已绑定引用，patch 目标必须是
`app.core.infra.tracing.*`（与 test_llm_langfuse 同理，patch langfuse
模块本身不会生效）。
"""

from __future__ import annotations

import time
from unittest.mock import patch

from app.core.infra import tracing


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


class _FakeClient:
    def __init__(self):
        self.spans: list[_FakeSpan] = []

    def start_observation(self, **kwargs) -> _FakeSpan:
        span = _FakeSpan(kwargs["name"])
        span.kwargs = kwargs
        self.spans.append(span)
        return span


def test_disabled_returns_none_without_touching_client():
    """未启用：返回 None，且不构造客户端（零开销）。"""
    with (
        patch.object(tracing, "is_langfuse_enabled", return_value=False),
        patch.object(tracing, "get_langfuse") as get_client,
    ):
        assert tracing.new_tool_span("query_order", {"order_sn": "20240801001"}) is None
        get_client.assert_not_called()


def test_finish_is_noop_for_none_span():
    """span 为 None 时 finish 直接返回（未启用路径不产生任何调用）。"""
    tracing.finish_tool_span(None, time.perf_counter(), result={"ok": 1})


def test_enabled_creates_named_span_with_arguments():
    client = _FakeClient()
    with (
        patch.object(tracing, "is_langfuse_enabled", return_value=True),
        patch.object(tracing, "get_langfuse", return_value=client),
    ):
        span = tracing.new_tool_span("query_order", {"order_sn": "20240801001"})

    assert span is not None
    assert span.name == "tool.query_order"
    assert span.kwargs["as_type"] == "span"
    assert span.kwargs["input"] == {"order_sn": "20240801001"}
    assert span.kwargs["metadata"] == {"tool": "query_order"}


def test_finish_records_output_and_success_metadata():
    client = _FakeClient()
    with (
        patch.object(tracing, "is_langfuse_enabled", return_value=True),
        patch.object(tracing, "get_langfuse", return_value=client),
    ):
        span = tracing.new_tool_span("query_order", {})
        tracing.finish_tool_span(span, time.perf_counter(), result={"status": "已发货"})

    assert span.ended is True
    update = span.updates[-1]
    assert update["output"] == {"status": "已发货"}
    assert update["metadata"]["ok"] is True
    assert update["metadata"]["elapsed_ms"] >= 0
    assert "level" not in update


def test_finish_marks_error_level_on_failure():
    client = _FakeClient()
    with (
        patch.object(tracing, "is_langfuse_enabled", return_value=True),
        patch.object(tracing, "get_langfuse", return_value=client),
    ):
        span = tracing.new_tool_span("create_ticket", {})
        tracing.finish_tool_span(
            span, time.perf_counter(), result={"error": "boom"}, error=RuntimeError("boom")
        )

    update = span.updates[-1]
    assert update["level"] == "ERROR"
    assert "boom" in update["status_message"]
    assert update["metadata"]["ok"] is False


def test_oversized_result_is_truncated():
    """大结果截断成字符串：避免整页订单列表撑爆上报体积。"""
    big = {"orders": ["x" * 100 for _ in range(100)]}

    truncated = tracing._truncate(big)

    assert isinstance(truncated, str)
    assert len(truncated) < 2500
    assert "截断" in truncated


def test_small_result_kept_as_is():
    assert tracing._truncate({"status": "已发货"}) == {"status": "已发货"}


def test_span_creation_failure_degrades_silently():
    """埋点初始化异常不能让工具调用失败：返回 None，调用方继续执行。"""
    with (
        patch.object(tracing, "is_langfuse_enabled", return_value=True),
        patch.object(tracing, "get_langfuse") as get_client,
    ):
        get_client.return_value.start_observation.side_effect = RuntimeError("otel down")

        assert tracing.new_tool_span("query_order", {}) is None


def test_finish_failure_degrades_silently():
    """span.update/end 抛异常时只记 warning，不向调用方传播。"""

    class _BrokenSpan:
        def update(self, **kwargs):
            raise RuntimeError("network")

        def end(self):
            raise RuntimeError("network")

    # 不抛异常即通过（内部已 try/except 兜底，埋点失败不能影响工具调用）
    tracing.finish_tool_span(_BrokenSpan(), time.perf_counter(), result={"ok": 1})
