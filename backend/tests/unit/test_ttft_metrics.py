"""T3 TTFT 与阶段耗时埋点单元测试。

覆盖（对齐 spec T3 验收标准）：
1. 正常 faq 链路稳定产出 ``ttft_ms`` 与 ``total_ms``（结构化日志 extra 字段）
2. 埋点抛异常时请求不受影响（旁路性：mock 掉 logger 让 info 抛异常，响应仍 200）
3. 降级路径（Reranker 跳排）仍能产出各阶段耗时（``stage_ms`` 含 rerank_ms）
4. 无流式增量（缓存命中）时 ttft_ms = -1，且不产生误导性的 TTFT 指标
5. engine.retrieve 的 stage_ms 字段齐全（rewrite/embed/retrieve/rerank/crag）

mock 策略：复用 test_chat_api 的 mock 方式（mock route / rag_engine，不连 LLM/向量库）。
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.rag import engine as real_rag_engine
from app.core.security.auth import create_access_token
from app.main import app

client = TestClient(app)

TEST_TOKEN = create_access_token({"uid": "tester-id", "sub": "tester", "role": "admin"})
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _disable_langfuse(monkeypatch):
    """禁用 chat trace 埋点（no-op 旁路），避免读取本机 .env 的真实 Langfuse key。"""
    from app.api import chat as chat_api

    monkeypatch.setattr(chat_api, "get_langfuse", lambda: None)


def _mock_engine() -> MagicMock:
    m = MagicMock()
    m.should_rewrite_retry = real_rag_engine.should_rewrite_retry
    m.retry_query_for = real_rag_engine.retry_query_for
    m.no_result_answer = real_rag_engine.no_result_answer
    return m


def _mock_generate_stream(mock_engine: MagicMock, answer: str = "这是 FAQ 答案") -> None:
    async def _gen(*args, **kwargs):
        if answer:
            yield {"delta": answer[:4]}
            yield {"delta": answer[4:]}
        yield {"final": {"answer": answer, "sources": [], "degraded": False}}

    mock_engine.generate_stream = _gen


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    events: list[tuple[str, dict | None]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event: str | None = None
        data: dict | None = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: "):].strip()
            elif line.startswith("data: "):
                raw = line[len("data: "):]
                data = json.loads(raw) if raw else None
        if event:
            events.append((event, data))
    return events


def _faq_engine() -> MagicMock:
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
            "crag": {"action": "generate", "score": 0.9, "degraded": False},
            "degraded": [],
            "stage_ms": {"rewrite_ms": 12, "embed_ms": 8, "retrieve_ms": 20, "rerank_ms": 30, "crag_ms": 15},
        }
    )
    _mock_generate_stream(mock_engine)
    return mock_engine


def _perf_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", "") == "chat_perf"]


# ---------- 1. 正常链路产出 ttft_ms ----------


def test_faq_chain_emits_ttft_and_stage_ms(caplog):
    mock_engine = _faq_engine()
    with patch(
        "app.api.chat.route",
        new=AsyncMock(return_value={"intent": "faq", "confidence": 1.0, "source": "rule", "low_confidence": False}),
    ), patch("app.api.chat.rag_engine", new=mock_engine), \
            caplog.at_level(logging.INFO, logger="app.api.chat"):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置系统"})

    assert resp.status_code == 200
    assert [e for e, _ in _parse_sse(resp.text)][-1] == "done"

    perfs = _perf_records(caplog)
    assert len(perfs) == 1
    rec = perfs[0]
    # ttft_ms / total_ms 均为整数
    assert isinstance(rec.ttft_ms, int) and rec.ttft_ms >= 0
    assert isinstance(rec.total_ms, int) and rec.total_ms >= 0
    # 阶段耗时齐全（engine.stage_ms 被回填为 recall_ms，避免与整段 retrieve_ms 混淆）
    for key in ("intent_ms", "rewrite_ms", "embed_ms", "recall_ms", "rerank_ms", "crag_ms", "retrieve_ms", "generate_ms"):
        assert isinstance(getattr(rec, key), int), key
    assert rec.recall_ms == 20
    # 日志正文含 *_ms（便于 grep / 压测脚本解析）
    assert "ttft_ms=" in rec.getMessage()


# ---------- 2. 埋点抛异常不影响请求 ----------


def test_perf_emit_failure_does_not_break_request(caplog):
    """mock logger.info 抛异常：`_emit_perf` 必须吞掉，SSE 事件序列与内容不受影响。"""
    mock_engine = _faq_engine()
    raising_logger = MagicMock()
    raising_logger.info.side_effect = RuntimeError("boom: 埋点故障")
    raising_logger.debug = logging.getLogger("test.noop").debug

    with patch(
        "app.api.chat.route",
        new=AsyncMock(return_value={"intent": "faq", "confidence": 1.0, "source": "rule", "low_confidence": False}),
    ), patch("app.api.chat.rag_engine", new=mock_engine), patch(
        "app.api.chat.logger", new=raising_logger
    ):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置系统"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert [e for e, _ in events][-1] == "done"
    assert "".join(d["delta"] for e, d in events if e == "delta") == "这是 FAQ 答案"


def test_metrics_write_failure_does_not_break_request(monkeypatch):
    """指标写入函数抛异常（模拟 exporter 故障）：请求仍成功，事件序列完整。"""
    from app.core.infra import metrics as metrics_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("metrics backend down")

    monkeypatch.setattr(metrics_mod, "observe_request", _boom)
    monkeypatch.setattr(metrics_mod, "observe_ttft", _boom)
    monkeypatch.setattr(metrics_mod, "inc_degradation", _boom)
    monkeypatch.setattr(metrics_mod, "inc_cache_hit", _boom)

    mock_engine = _faq_engine()
    with patch(
        "app.api.chat.route",
        new=AsyncMock(return_value={"intent": "faq", "confidence": 1.0, "source": "rule", "low_confidence": False}),
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置系统"})

    assert resp.status_code == 200
    assert [e for e, _ in _parse_sse(resp.text)][-1] == "done"


# ---------- 3. 降级路径仍产出耗时 ----------


async def test_engine_stage_ms_on_reranker_degradation(
    mock_embedding, mock_qdrant, bm25_with_docs, mock_reranker_fail
):
    """Reranker 降级（跳排用 RRF）时仍产出 rerank_ms，且 degraded 记录 reranker。"""
    with patch("app.core.rag.engine.rewrite_query", new=AsyncMock(return_value={
        "original": "AssistMind", "variants": [], "hyde": None,
        "all_queries": ["AssistMind"], "degraded": False,
    })), patch("app.core.rag.engine.crag_evaluate", new=AsyncMock(return_value={
        "score": 0.8, "action": "generate", "degraded": False,
    })):
        result = await real_rag_engine.retrieve("AssistMind")

    assert "reranker" in result["degraded"]
    stage_ms = result["stage_ms"]
    for key in ("rewrite_ms", "embed_ms", "retrieve_ms", "rerank_ms", "crag_ms"):
        assert isinstance(stage_ms[key], int), key
    assert stage_ms["rerank_ms"] >= 0


# ---------- 4. 无流式增量时 ttft_ms = -1 ----------


def test_cache_hit_ttft_is_negative_one(caplog):
    """缓存命中无 delta 事件：ttft_ms=-1（无首 token 概念），且命中 L1 计数有日志。"""
    mock_engine = _mock_engine()
    with patch(
        "app.api.chat.route",
        new=AsyncMock(return_value={"intent": "faq", "confidence": 1.0, "source": "rule", "low_confidence": False}),
    ), patch("app.api.chat.rag_engine", new=mock_engine), patch(
        "app.api.chat.semantic_cache"
    ) as cache:
        cache.get = AsyncMock(return_value={"answer": "缓存答案", "sources": [], "from_cache": "L1"})
        with caplog.at_level(logging.INFO, logger="app.api.chat"):
            resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "退货政策"})

    assert resp.status_code == 200
    perfs = _perf_records(caplog)
    assert len(perfs) == 1
    assert perfs[0].ttft_ms == -1
    # 缓存命中路径不得触碰 retrieve/generate
    mock_engine.retrieve.assert_not_called()
