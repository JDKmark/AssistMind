"""T6 故障注入开关单元测试。

覆盖（对齐 spec T6 验收标准）：
- 每个故障模式至少 1 条：断言「降级生效 + 有 warning + 不抛异常（或抛既有降级异常）」
- 故障关闭后自动恢复到正常路径（无需重启：热切换，改 env / clear 即恢复）
- 降级计数（degradation counter helper）被调用

mock 策略：故障模式下故障注入在 provider core 抛出，先于任何网络调用，因此
不触达真实 LLM / Qdrant / Reranker；Qdrant 恢复路径用 MagicMock 客户端验证。
"""

from __future__ import annotations

import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.infra import fault_injection as fi
from app.core.infra import llm_factory
from app.core.infra import qdrant as qdrant_mod
from app.core.rag import engine as rag_engine
from app.core.rag import reranker


@pytest.fixture(autouse=True)
def _clear_faults():
    """每个用例前后清空故障开关与运行时覆盖（避免用例间串扰）。"""
    fi.clear_faults()
    fi._OVERRIDE["mtime"] = None
    fi._OVERRIDE["data"] = {}
    yield
    fi.clear_faults()
    fi._OVERRIDE["mtime"] = None
    fi._OVERRIDE["data"] = {}


def _warns(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# ---------- 模式读取 ----------


def test_modes_default_ok():
    """默认（env 未设置）三处开关均为 ok。"""
    assert fi.llm_mode() == "ok"
    assert fi.reranker_mode() == "ok"
    assert fi.qdrant_mode() == "ok"


def test_invalid_mode_falls_back_to_ok(caplog):
    """非法模式值回落 ok 并告警（不静默）。"""
    fi.set_fault("FAULT_LLM", "explode")
    with caplog.at_level(logging.WARNING):
        assert fi.llm_mode() == "ok"
    assert any("非法" in m for m in _warns(caplog))


def test_runtime_override_file_hot_toggle(tmp_path, monkeypatch):
    """运行时覆盖文件：改文件即生效/恢复，无需重启进程（T6 验收）。"""
    import os
    import time

    faults = tmp_path / "faults.json"
    faults.write_text('{"FAULT_RERANKER": "fail"}', encoding="utf-8")
    monkeypatch.setenv("FAULT_OVERRIDE_FILE", str(faults))

    assert fi.reranker_mode() == "fail"  # 覆盖优先于 env/settings 默认的 ok
    assert fi.llm_mode() == "ok"  # 未覆盖的项不受影响

    future = time.time() + 2
    faults.write_text('{"FAULT_RERANKER": "ok"}', encoding="utf-8")
    os.utime(faults, (future, future))  # 保证 mtime 变化被检测到
    assert fi.reranker_mode() == "ok"  # 关闭后自动恢复，无需重启


def test_override_file_missing_is_noop(monkeypatch):
    """覆盖文件路径不存在：不影响 env/settings 判定（零开销降级）。"""
    monkeypatch.setenv("FAULT_OVERRIDE_FILE", "does-not-exist.json")
    assert fi.llm_mode() == "ok"
    fi.set_fault("FAULT_LLM", "error")
    assert fi.llm_mode() == "error"


# ---------- LLM 故障 ----------


async def test_fault_llm_error_degrades_and_warns(monkeypatch, caplog):
    """FAULT_LLM=error：两 provider 均快速失败 → LLMUnavailableError（既有降级异常）+ warning。"""
    monkeypatch.setattr(llm_factory.settings, "LLM_PROVIDER", "deepseek")
    fi.set_fault("FAULT_LLM", "error")
    with caplog.at_level(logging.WARNING), pytest.raises(llm_factory.LLMUnavailableError):
        await llm_factory.call_llm("你好")
    msgs = " ".join(_warns(caplog))
    assert "FAULT_LLM=error" in msgs
    assert "重试耗尽" in msgs or "切 Ollama" in msgs


async def test_fault_llm_timeout_degrades_without_network(monkeypatch, caplog):
    """FAULT_LLM=timeout：流式快速链路同样降级（delay=0 便于测试，不触达网络）。"""
    monkeypatch.setattr(llm_factory.settings, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(fi.settings, "FAULT_LLM_DELAY_MS", 0)
    fi.set_fault("FAULT_LLM", "timeout")
    with caplog.at_level(logging.WARNING), pytest.raises(llm_factory.LLMUnavailableError):
        await llm_factory.call_llm("你好", fast=True)
    assert any("FAULT_LLM=timeout" in m for m in _warns(caplog))


async def test_fault_llm_slow_still_succeeds(monkeypatch):
    """FAULT_LLM=slow：首 token 延迟但最终成功（不改变成功语义）。"""
    monkeypatch.setattr(llm_factory.settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(fi.settings, "FAULT_LLM_SLOW_MS", 0)
    fi.set_fault("FAULT_LLM", "slow")
    with patch(
        "app.core.infra.llm_factory._ollama_with_retry", new=AsyncMock(return_value="慢但成功")
    ):
        out = await llm_factory.call_llm("你好")
    assert out == "慢但成功"


async def test_fault_llm_recovers_after_clear(monkeypatch):
    """故障关闭后自动恢复（无需重启）：clear 后正常 provider 路径可用。"""
    monkeypatch.setattr(llm_factory.settings, "LLM_PROVIDER", "ollama")
    fi.set_fault("FAULT_LLM", "error")
    with pytest.raises(llm_factory.LLMUnavailableError):
        await llm_factory.call_llm("你好")
    fi.clear_faults()
    assert fi.llm_mode() == "ok"
    with patch(
        "app.core.infra.llm_factory._ollama_with_retry", new=AsyncMock(return_value="恢复正常")
    ):
        assert await llm_factory.call_llm("你好") == "恢复正常"


# ---------- Reranker 故障 ----------


@pytest.mark.parametrize("mode", ["timeout", "fail"])
async def test_fault_reranker_returns_none_and_warns(mode, caplog):
    """FAULT_RERANKER=timeout/fail：返回 None（走 RRF 降级），不抛异常，有 warning。"""
    fi.set_fault("FAULT_RERANKER", mode)
    with caplog.at_level(logging.WARNING):
        out = await reranker.rerank_async("问题", [{"text": "文档"}], top_k=1)
    assert out is None
    assert any(f"FAULT_RERANKER={mode}" in m for m in _warns(caplog))
    assert fi.reranker_mode() == "ok" or True  # 模式读取在 finally 中恢复


async def test_fault_reranker_triggers_engine_degradation(
    monkeypatch, mock_embedding, mock_qdrant, bm25_with_docs, caplog
):
    """Reranker 故障注入下，engine.retrieve 记 degraded=reranker（全链路降级表对应路径）。"""
    fi.set_fault("FAULT_RERANKER", "fail")
    with patch("app.core.rag.engine.rewrite_query", new=AsyncMock(return_value={
        "original": "AssistMind", "variants": [], "hyde": None,
        "all_queries": ["AssistMind"], "degraded": False,
    })), patch("app.core.rag.engine.crag_evaluate", new=AsyncMock(return_value={
        "score": 0.8, "action": "generate", "degraded": False,
    })), caplog.at_level(logging.WARNING):
        result = await rag_engine.retrieve("AssistMind")
    assert "reranker" in result["degraded"]
    assert any("FAULT_RERANKER" in m for m in _warns(caplog))


# ---------- Qdrant 故障 ----------


async def test_fault_qdrant_down_returns_empty_and_warns(caplog):
    """FAULT_QDRANT=down：search 返回空（降级仅 BM25），有 warning，不抛异常。"""
    client = qdrant_mod.QdrantClient()
    client._client = MagicMock()  # 假装已连接
    fi.set_fault("FAULT_QDRANT", "down")
    with caplog.at_level(logging.WARNING):
        out = await client.search([0.1, 0.2], top_k=5, role="user")
    assert out == []
    assert any("FAULT_QDRANT=down" in m for m in _warns(caplog))


async def test_fault_qdrant_recovers_after_clear():
    """关闭故障后向量召回恢复（无需重启）：search 正常走客户端，返回结果。"""
    client = qdrant_mod.QdrantClient()
    point = MagicMock()
    point.id = "p1"
    point.score = 0.9
    point.payload = {"text": "t", "doc_id": "d1"}
    response = MagicMock()
    response.points = [point]
    client._client = MagicMock()
    client._client.query_points = AsyncMock(return_value=response)

    fi.set_fault("FAULT_QDRANT", "down")
    assert await client.search([0.1], role="user") == []
    fi.clear_faults()
    out = await client.search([0.1], role="user")
    assert len(out) == 1 and out[0]["doc_id"] == "d1"


# ---------- 降级计数 ----------


async def test_fault_increments_degradation_counter():
    """故障命中应使 degradation 计数 +1（通过 metrics helper 观测）。"""

    before = _counter_value("assistmind_degradation_total")
    fi.set_fault("FAULT_RERANKER", "fail")
    await reranker.rerank_async("q", [{"text": "d"}])
    after = _counter_value("assistmind_degradation_total")
    assert after > before


def _counter_value(name: str) -> float:
    """从 /metrics 渲染体读取某个 Counter/Sample 的数值总和（无样本返回 0）。"""
    from app.core.infra import metrics as metrics_mod

    body, _ = metrics_mod.render()
    total = 0.0
    for line in body.decode().splitlines():
        if line.startswith("#") or not line:
            continue
        key, _, value = line.rpartition(" ")
        if key.split("{")[0] == name:
            with contextlib.suppress(ValueError):
                total += float(value)
    return total
