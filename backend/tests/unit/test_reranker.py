"""SiliconFlow 云端 rerank 单元测试。

mock httpx.AsyncClient（不连真实网络）；Settings 显式构造（防 .env 污染，
见 AGENTS spec 复盘「测试环境污染」）。覆盖：
1. 成功：分数映射（index→doc）、按分排序、top_n 截断、鉴权头/请求体形状
2. 未配 key：返回 None 且不发起 HTTP
3. 网络错误 / HTTP 5xx：返回 None（降级跳过重排用 RRF）
4. RERANKER_ENABLED=false：直接 None
5. provider=local：走本地路径，不发起 HTTP
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import Settings
from app.core.rag import reranker as reranker_module

DOCS = [
    {"id": "a", "text": "退货政策说明"},
    {"id": "b", "text": "物流时效说明"},
    {"id": "c", "text": "会员积分说明"},
]


def _make_settings(provider="siliconflow", key="sk-test", enabled=True):
    return Settings(
        RERANKER_PROVIDER=provider,
        SILICONFLOW_API_KEY=key,
        RERANKER_ENABLED=enabled,
    )


@pytest.fixture
def siliconflow_settings(monkeypatch):
    settings = _make_settings()
    monkeypatch.setattr(reranker_module, "settings", settings)
    return settings


def _install_fake_client(monkeypatch, payload=None, exc=None, status_code=200):
    """替换 httpx.AsyncClient 为受控假客户端，返回调用记录列表。"""
    calls: list[dict] = []

    class _FakeResponse:
        def __init__(self):
            self.status_code = status_code

        def raise_for_status(self):
            if status_code >= 400:
                request = httpx.Request("POST", "https://api.siliconflow.cn/v1/rerank")
                response = httpx.Response(status_code, request=request)
                raise httpx.HTTPStatusError(
                    f"HTTP {status_code}", request=request, response=response
                )

        def json(self):
            return payload

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, headers=None, json=None):
            calls.append({"url": url, "headers": headers, "json": json})
            if exc is not None:
                raise exc
            return _FakeResponse()

    monkeypatch.setattr(reranker_module.httpx, "AsyncClient", _FakeAsyncClient)
    return calls


async def test_siliconflow_rerank_success(siliconflow_settings, monkeypatch):
    """成功：index 映射回文档、按分排序、top_n 截断、请求体/鉴权头形状正确。"""
    payload = {"results": [
        {"index": 2, "relevance_score": 0.92},
        {"index": 0, "relevance_score": 0.41},
    ]}
    calls = _install_fake_client(monkeypatch, payload=payload)

    result = await reranker_module.rerank_async("退货政策", DOCS, top_k=2)

    assert result is not None
    assert [d["id"] for d in result] == ["c", "a"]
    assert result[0]["rerank_score"] == pytest.approx(0.92)
    assert len(calls) == 1
    assert calls[0]["json"]["model"] == "BAAI/bge-reranker-v2-m3"
    assert calls[0]["json"]["top_n"] == 2
    assert calls[0]["json"]["documents"] == [d["text"] for d in DOCS]
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-test"


async def test_siliconflow_rerank_missing_key_returns_none(monkeypatch):
    """未配 key：返回 None 且不发起 HTTP（避免静默走本地慢路径）。"""
    monkeypatch.setattr(reranker_module, "settings", _make_settings(key=""))
    calls = _install_fake_client(monkeypatch, payload={"results": []})

    result = await reranker_module.rerank_async("q", DOCS, top_k=8)

    assert result is None
    assert calls == []


async def test_siliconflow_rerank_network_error_returns_none(
    siliconflow_settings, monkeypatch
):
    """网络错误：返回 None（降级跳过重排）。"""
    calls = _install_fake_client(monkeypatch, exc=httpx.ConnectError("boom"))

    result = await reranker_module.rerank_async("q", DOCS, top_k=8)

    assert result is None
    assert len(calls) == 1


async def test_siliconflow_rerank_timeout_returns_none(siliconflow_settings, monkeypatch):
    """wait_for 硬护栏：请求挂起超时 → 返回 None（跳过重排用 RRF），不拖死整条 SSE。

    回归背景：siliconflow 免费档偶发网络挂起，httpx timeout 未能截断（曾拖死 faq
    首问 180s+）；wait_for 强制截断并走既有降级路径。
    """
    _install_fake_client(monkeypatch, payload={"results": []})

    async def _hang(coro, timeout=None):
        # 先消费 coro（避免 "coroutine was never awaited" 告警），再模拟超时截断
        try:
            await coro
        except Exception:
            pass
        raise asyncio.TimeoutError()

    monkeypatch.setattr(reranker_module.asyncio, "wait_for", _hang)

    result = await reranker_module.rerank_async("q", DOCS, top_k=8)

    assert result is None


async def test_siliconflow_rerank_http_500_returns_none(siliconflow_settings, monkeypatch):
    """HTTP 5xx：raise_for_status 抛错 → 返回 None。"""
    calls = _install_fake_client(monkeypatch, payload={}, status_code=500)

    result = await reranker_module.rerank_async("q", DOCS, top_k=8)

    assert result is None
    assert len(calls) == 1


async def test_siliconflow_rerank_bad_index_skipped(siliconflow_settings, monkeypatch):
    """越界 index：跳过该条，不崩溃。"""
    payload = {"results": [
        {"index": 99, "relevance_score": 0.9},
        {"index": 1, "relevance_score": 0.3},
    ]}
    _install_fake_client(monkeypatch, payload=payload)

    result = await reranker_module.rerank_async("q", DOCS, top_k=8)

    assert [d["id"] for d in result] == ["b"]


async def test_rerank_disabled_returns_none(monkeypatch):
    """RERANKER_ENABLED=false：直接 None，不发起 HTTP。"""
    monkeypatch.setattr(reranker_module, "settings", _make_settings(enabled=False))
    calls = _install_fake_client(monkeypatch, payload={"results": []})

    result = await reranker_module.rerank_async("q", DOCS, top_k=8)

    assert result is None
    assert calls == []


async def test_local_provider_uses_local_path(monkeypatch):
    """provider=local：走本地路径（stub _predict_sync），不发起 HTTP。"""
    monkeypatch.setattr(reranker_module, "settings", _make_settings(provider="local"))
    calls = _install_fake_client(monkeypatch, payload={"results": []})

    def fake_predict_sync(query, docs, top_k):
        return docs[:top_k]

    monkeypatch.setattr(reranker_module, "_predict_sync", fake_predict_sync)

    result = await reranker_module.rerank_async("q", DOCS, top_k=2)

    assert [d["id"] for d in result] == ["a", "b"]
    assert calls == []
