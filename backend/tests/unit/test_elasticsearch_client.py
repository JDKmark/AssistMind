"""Elasticsearch 客户端单元测试。

mock 策略：直接注入 httpx.MockTransport 模拟 HTTP 响应，不连真实服务。
"""

from __future__ import annotations

from unittest.mock import patch

import httpx

from app.core.infra import elasticsearch as es_module
from app.core.infra.elasticsearch import ElasticsearchClient


def _client_with(handler) -> ElasticsearchClient:
    client = ElasticsearchClient()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return client


async def test_search_parses_hits():
    """_search 返回 hits 的 _source 列表，并注入 size。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode("utf-8")
        assert '"size":30' in body  # 客户端注入 size（httpx 紧凑 JSON）
        assert request.url.path.endswith("/_search")  # * 会被 httpx 编码，不断言全路径
        return httpx.Response(
            200,
            json={
                "hits": {
                    "total": {"value": 2},
                    "hits": [
                        {"_index": "logs-2026.08", "_source": {"@timestamp": "2026-08-05T01:00:00Z", "message": "conn pool exhausted"}},
                        {"_index": "logs-2026.08", "_source": {"@timestamp": "2026-08-05T00:59:00Z", "message": "timeout"}},
                    ],
                }
            },
        )

    client = _client_with(handler)
    sources = await client.search("logs-*", {"query": {"match_all": {}}}, size=30)
    assert len(sources) == 2
    assert sources[0]["message"] == "conn pool exhausted"


async def test_search_failure_returns_empty():
    """HTTP/网络异常返回 []（失败降级，不抛异常）。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client_with(handler)
    assert await client.search("logs-*", {}) == []


async def test_search_breaker_open_skips():
    """断路器 Open 时直接返回 []，不发起请求。"""
    client = _client_with(lambda request: httpx.Response(200, json={"hits": {"hits": []}}))
    with patch("app.core.infra.elasticsearch.is_open", return_value=True):
        assert await client.search("logs-*", {}) == []


async def test_search_not_connected():
    """未连接（无客户端）返回 []。"""
    client = ElasticsearchClient()
    assert await client.search("logs-*", {}) == []


async def test_connect_without_url(monkeypatch):
    """未配置 ELASTICSEARCH_URL 时 connect 后不可用。"""
    monkeypatch.setattr(es_module.settings, "ELASTICSEARCH_URL", "")
    client = ElasticsearchClient()
    await client.connect()
    assert not client.is_connected
    assert await client.search("logs-*", {}) == []


async def test_connect_with_auth(monkeypatch):
    """配置 basic auth 时客户端创建成功。"""
    monkeypatch.setattr(es_module.settings, "ELASTICSEARCH_URL", "http://es:9200")
    monkeypatch.setattr(es_module.settings, "ELASTICSEARCH_USERNAME", "elastic")
    monkeypatch.setattr(es_module.settings, "ELASTICSEARCH_PASSWORD", "secret")
    client = ElasticsearchClient()
    await client.connect()
    assert client.is_connected
    # httpx 将 (user, pass) 元组规范化为 BasicAuth，凭据存在 _auth_header（base64）
    import base64

    header = client._client.auth._auth_header
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
    assert decoded == "elastic:secret"
    await client.close()
