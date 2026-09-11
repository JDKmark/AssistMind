"""T5 应用级 Prometheus /metrics 单元测试。

覆盖（对齐 spec T5 验收标准）：
1. GET /metrics 返回全部约定指标（assistmind_*，10 个）
2. 指标在真实请求后数值变化（request/ttft/cache/stage 计数出现）
3. 未启用指标（METRICS_ENABLED=false）时整体 no-op，端点不报错
4. 禁止高基数标签：query 原文不得出现在指标中（信息泄漏 + 基数爆炸防护）
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.rag import engine as real_rag_engine
from app.core.security.auth import create_access_token
from app.main import app

client = TestClient(app)

TEST_TOKEN = create_access_token({"uid": "tester-id", "sub": "tester", "role": "admin"})
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}

_EXPECTED_METRICS = (
    "assistmind_inflight_requests",
    "assistmind_inflight_rejected_total",
    "assistmind_request_duration_seconds",
    "assistmind_ttft_seconds",
    "assistmind_stage_duration_seconds",
    "assistmind_cache_hit_total",
    "assistmind_degradation_total",
    "assistmind_breaker_state",
    "assistmind_llm_upstream_errors_total",
    "assistmind_rate_limited_total",
)


def test_metrics_endpoint_lists_all_assistmind_metrics():
    """端点可用且注册了全部约定指标（HELP 行包含 10 个 assistmind_* 名）。"""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    for name in _EXPECTED_METRICS:
        assert name in body, name


def test_metrics_values_change_after_chat_request():
    """一次 faq 请求后，request/ttft/cache 指标出现对应标签的样本。"""
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
            "stage_ms": {"rewrite_ms": 1, "embed_ms": 1, "retrieve_ms": 1, "rerank_ms": 1, "crag_ms": 1},
        }
    )

    async def _gen(*args, **kwargs):
        yield {"delta": "答案"}
        yield {"final": {"answer": "答案", "sources": [], "degraded": False}}

    mock_engine.generate_stream = _gen

    from app.api import chat as chat_api

    with patch.object(chat_api, "get_langfuse", lambda: None), patch(
        "app.api.chat.route",
        new=AsyncMock(return_value={"intent": "faq", "confidence": 1.0, "source": "rule", "low_confidence": False}),
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置系统"})
    assert resp.status_code == 200

    body = client.get("/metrics").text
    assert 'assistmind_request_duration_seconds_count{intent="faq"}' in body
    assert 'assistmind_ttft_seconds_count{intent="faq"}' in body
    assert 'assistmind_stage_duration_seconds_count{stage="generate"}' in body
    assert 'assistmind_cache_hit_total{level="miss"' in body


def test_metrics_disabled_is_noop(monkeypatch):
    """METRICS_ENABLED=false：端点返回 200 且为禁用占位体（不抛异常、不影响应用）。"""
    from app.core.infra import metrics as metrics_mod

    monkeypatch.setattr(metrics_mod, "settings", SimpleNamespace(METRICS_ENABLED=False))
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "metrics disabled" in resp.text


def test_query_text_not_in_labels():
    """禁止高基数/泄漏：请求 query 原文不得出现在指标输出中。"""
    marker = "SENSITIVE_QUERY_MARKER_9f3a"
    mock_engine = MagicMock()
    mock_engine.should_rewrite_retry = real_rag_engine.should_rewrite_retry
    mock_engine.retry_query_for = real_rag_engine.retry_query_for
    mock_engine.no_result_answer = real_rag_engine.no_result_answer
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [],
            "crag": {"action": "no_result", "score": 0.0, "degraded": False},
            "degraded": [],
            "stage_ms": {"rewrite_ms": 1, "embed_ms": 1, "retrieve_ms": 1, "rerank_ms": 1, "crag_ms": 1},
        }
    )

    from app.api import chat as chat_api

    with patch.object(chat_api, "get_langfuse", lambda: None), patch(
        "app.api.chat.route",
        new=AsyncMock(return_value={"intent": "faq", "confidence": 1.0, "source": "rule", "low_confidence": False}),
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": marker})

    assert marker not in client.get("/metrics").text


def test_metrics_body_is_parseable():
    """渲染体为合法 Prometheus 文本（可被解析为 name -> value）。"""
    body = client.get("/metrics").text
    samples = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) == 2:
            samples[parts[0]] = parts[1]
    assert "assistmind_inflight_requests" in samples
    assert json.dumps(samples)  # 值均为可序列化文本
