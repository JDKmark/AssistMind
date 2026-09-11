"""情绪安全阀单元测试（persona-upgrade T4）。

覆盖：
- emotion_override 规则层检测：负面关键词命中 / 感叹号连续 ≥2 命中 /
  非连续感叹号不命中 / 正常查询与空查询不命中
- 注入顺序：既有 system → override → persona（chat 与 faq 两条链路验证）
- 命中时 logger.info + Langfuse span metadata 记 emotion_override=True
  （未启用 Langfuse 时 no-op）

API 级测试 mock 策略与 test_chat_api.py 一致：patch route / stream_llm /
rag_engine / get_langfuse，SSE 全文读取后解析。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.personas import emotion_override
from app.core.rag import engine as real_rag_engine
from app.core.security.auth import create_access_token
from app.main import app

client = TestClient(app)

TEST_TOKEN = create_access_token({"uid": "tester-id", "sub": "tester", "role": "admin"})
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}

_CHAT_SYSTEM_PREFIX = "你是 AssistMind 智能客服"
_OVERRIDE_MARK = "共情安抚"  # 共情优先指令的特征片段
_PERSONA_MARK = "活泼客服"  # lively 人格指令的特征片段


# ---------- 规则层检测 ----------


def test_negative_keyword_hit():
    result = emotion_override("你们这什么破服务，我要投诉")
    assert result is not None
    assert "共情" in result
    assert "让位于该原则" in result


def test_exclamation_run_hit_fullwidth():
    assert emotion_override("太离谱了！！") is not None


def test_exclamation_run_hit_halfwidth_and_mixed():
    assert emotion_override("so bad!!") is not None
    assert emotion_override("离谱！!") is not None  # 全半角混排连续


def test_non_consecutive_exclamation_not_hit():
    """感叹号不连续（间隔出现）不触发——只在情绪激动连打时让位。"""
    assert emotion_override("好!棒!") is None


def test_normal_query_not_hit():
    assert emotion_override("查一下订单物流到哪了") is None


def test_empty_query_not_hit():
    assert emotion_override("") is None


# ---------- 注入顺序（chat 链路）----------


def _capture_stream_llm():
    """mock stream_llm：记录每次调用 kwargs，yield 两段 delta。"""
    calls: list[dict] = []

    async def _stream(prompt, system=None, generation=False, **kwargs):
        calls.append({"prompt": prompt, "system": system, **kwargs})
        for chunk in ("您好", "呀"):
            yield chunk

    return calls, _stream


def test_chat_intent_override_injected_before_persona():
    """chat 意图命中情绪安全阀：注入顺序为 既有 system → override → persona。"""
    calls, stream = _capture_stream_llm()
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "chat",
                "confidence": 0.9,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.stream_llm", side_effect=stream), patch(
        "app.api.chat.get_langfuse", lambda: None
    ):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={"query": "你们太差了，我要投诉", "persona": "lively"},
        )

    assert resp.status_code == 200
    system = calls[-1]["system"]
    assert system is not None
    assert system.startswith(_CHAT_SYSTEM_PREFIX)  # 既有 system 在最前
    assert _OVERRIDE_MARK in system
    assert _PERSONA_MARK in system
    # override 在 persona 之前（人格让位）
    assert system.index(_OVERRIDE_MARK) < system.index(_PERSONA_MARK)


# ---------- 注入顺序（faq 链路）----------


def _mock_engine_capture_generate():
    """faq 引擎 mock：CRAG 决策绑真实纯函数，generate_stream 记录 kwargs。"""
    mock_engine = MagicMock()
    mock_engine.should_rewrite_retry = real_rag_engine.should_rewrite_retry
    mock_engine.retry_query_for = real_rag_engine.retry_query_for
    mock_engine.no_result_answer = real_rag_engine.no_result_answer
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
            "crag": {"action": "generate", "score": 0.9, "degraded": False},
            "degraded": [],
        }
    )
    captured: dict = {}

    async def _gen(*args, **kwargs):
        captured.update(kwargs)
        yield {"delta": "已收到"}
        yield {"final": {"answer": "已收到您的反馈", "sources": [], "degraded": False}}

    mock_engine.generate_stream = _gen
    return mock_engine, captured


def test_faq_intent_override_injected_before_persona():
    """faq 意图命中情绪安全阀：generate_stream 的 system_suffix 为 override → persona。"""
    mock_engine, captured = _mock_engine_capture_generate()
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine), patch(
        "app.api.chat.get_langfuse", lambda: None
    ):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={"query": "太差了！！我要投诉", "persona": "lively"},
        )

    assert resp.status_code == 200
    system_suffix = captured.get("system_suffix")
    assert system_suffix
    assert _OVERRIDE_MARK in system_suffix
    assert _PERSONA_MARK in system_suffix
    assert system_suffix.index(_OVERRIDE_MARK) < system_suffix.index(_PERSONA_MARK)


# ---------- 观测：logger.info + Langfuse span metadata ----------


class _RecordingSpan:
    """记录 update() 调用 kwargs 的假 span（trace_id 属性，仿 langfuse 4.14）。"""

    def __init__(self):
        self.trace_id = "trace-emotion-1"
        self.updates: list[dict] = []

    def set_trace_io(self, **kwargs):
        pass

    def update(self, **kwargs):
        self.updates.append(kwargs)


class _RecordingLangfuse:
    def __init__(self, span: _RecordingSpan):
        self._span = span

    def start_as_current_observation(self, **kwargs):
        @contextmanager
        def _body():
            yield self._span

        return _body()


def test_emotion_override_hit_logged_and_recorded_in_span_metadata(caplog):
    """命中时：logger.info 人格让位 + span metadata 记 emotion_override=True。"""
    calls, stream = _capture_stream_llm()
    span = _RecordingSpan()
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "chat",
                "confidence": 0.9,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.stream_llm", side_effect=stream), patch(
        "app.api.chat.get_langfuse", lambda: _RecordingLangfuse(span)
    ):
        caplog.set_level(logging.INFO)
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={"query": "我要投诉你们", "persona": "lively"},
        )

    assert resp.status_code == 200
    # logger.info（不静默）
    assert any("情绪安全阀触发" in r.message for r in caplog.records)
    # Langfuse span metadata（启用时记录；未启用时 span 为 None 自然 no-op）
    metadata_updates = [u for u in span.updates if "metadata" in u]
    assert metadata_updates, "span.update(metadata=...) 应至少被调用一次"
    assert all(
        u["metadata"].get("emotion_override") is True for u in metadata_updates
    )


def test_emotion_override_not_hit_no_metadata_flag():
    """未命中情绪安全阀：span metadata 不带 emotion_override 键。"""
    calls, stream = _capture_stream_llm()
    span = _RecordingSpan()
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "chat",
                "confidence": 0.9,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.stream_llm", side_effect=stream), patch(
        "app.api.chat.get_langfuse", lambda: _RecordingLangfuse(span)
    ):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={"query": "你好呀", "persona": "lively"},
        )

    assert resp.status_code == 200
    for u in span.updates:
        if "metadata" in u:
            assert "emotion_override" not in u["metadata"]
