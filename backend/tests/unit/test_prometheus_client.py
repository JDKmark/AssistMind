"""Prometheus 客户端单元测试。

mock 策略：直接注入 httpx.MockTransport 模拟 HTTP 响应，不连真实服务。
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.core.infra import prometheus as prom_module
from app.core.infra.prometheus import PrometheusClient


def _client_with(handler) -> PrometheusClient:
    """构造客户端并注入 MockTransport（绕过 connect）。"""
    client = PrometheusClient()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return client


def _range_ok_handler() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/query_range":
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "matrix",
                        "result": [
                            {
                                "metric": {"service": "order-service"},
                                "values": [[1700000000, "1.5"], [1700000060, "2.5"]],
                            }
                        ],
                    },
                },
            )
        if request.url.path.startswith("/api/v1/label/"):
            return httpx.Response(
                200, json={"status": "success", "data": ["order-service", "api-gateway"]}
            )
        return httpx.Response(404, json={"status": "error"})

    return handler


async def test_query_range_parses_points():
    """Range Query 响应解析为 [{ts, value}]，value 字符串转 float。"""
    client = _client_with(_range_ok_handler())
    points = await client.query_range("ops_error_rate", 1700000000, 1700000060, step=60)
    assert points == [{"ts": 1700000000, "value": 1.5}, {"ts": 1700000060, "value": 2.5}]


async def test_query_range_empty_result():
    """result 为空返回 []。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "matrix", "result": []}}
        )

    client = _client_with(handler)
    assert await client.query_range("x", 1, 100) == []


async def test_query_range_status_not_success():
    """status != success 返回 []。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "errorType": "bad_data"})

    client = _client_with(handler)
    assert await client.query_range("x", 1, 100) == []


async def test_query_range_http_error_returns_empty():
    """HTTP 5xx 返回 []（失败降级，不抛异常）。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"status": "error"})

    client = _client_with(handler)
    assert await client.query_range("x", 1, 100) == []


async def test_query_range_network_error_returns_empty():
    """网络异常返回 []。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client_with(handler)
    assert await client.query_range("x", 1, 100) == []


async def test_query_range_breaker_open_skips():
    """断路器 Open 时直接返回 []，不发起请求。"""
    client = _client_with(_range_ok_handler())
    with patch("app.core.infra.prometheus.is_open", return_value=True):
        assert await client.query_range("x", 1, 100) == []


async def test_label_values():
    """label values 返回字符串列表。"""
    client = _client_with(_range_ok_handler())
    values = await client.label_values("service")
    assert values == ["order-service", "api-gateway"]


async def test_label_values_failure_returns_empty():
    """label values 失败返回 []。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client_with(handler)
    assert await client.label_values("service") == []


async def test_query_range_not_connected():
    """未连接（无客户端）返回 []。"""
    client = PrometheusClient()
    assert await client.query_range("x", 1, 100) == []
    assert await client.label_values("service") == []


async def test_connect_without_url(monkeypatch):
    """未配置 PROMETHEUS_URL 时 connect 后不可用。"""
    monkeypatch.setattr(prom_module.settings, "PROMETHEUS_URL", "")
    client = PrometheusClient()
    await client.connect()
    assert not client.is_connected
    assert await client.query_range("x", 1, 100) == []


async def test_ping(monkeypatch):
    """健康探测：200 返回 True，异常返回 False。"""
    async def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Prometheus Server is Healthy.")

    ok_client = _client_with(ok_handler)
    assert await ok_client.ping() is True

    async def fail_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    fail_client = _client_with(fail_handler)
    assert await fail_client.ping() is False

    not_connected = PrometheusClient()
    assert await not_connected.ping() is False
