"""Alertmanager 客户端单元测试。

mock 策略：直接注入 httpx.MockTransport 模拟 HTTP 响应，不连真实服务。
"""

from __future__ import annotations

from unittest.mock import patch

import httpx

from app.core.infra import alertmanager as am_module
from app.core.infra.alertmanager import AlertmanagerClient


def _client_with(handler) -> AlertmanagerClient:
    client = AlertmanagerClient()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return client


async def test_alerts_returns_raw_list():
    """alerts 返回原始 Alertmanager 结构列表。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/alerts"
        return httpx.Response(
            200,
            json=[
                {
                    "fingerprint": "abc123",
                    "labels": {"alertname": "HighErrorRate", "service": "order-service"},
                    "annotations": {"summary": "错误率过高"},
                    "startsAt": "2026-08-05T01:00:00Z",
                }
            ],
        )

    client = _client_with(handler)
    alerts = await client.alerts()
    assert len(alerts) == 1
    assert alerts[0]["labels"]["alertname"] == "HighErrorRate"


async def test_alerts_failure_returns_empty():
    """网络异常返回 []（失败降级，不抛异常）。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client_with(handler)
    assert await client.alerts() == []


async def test_alerts_breaker_open_skips():
    """断路器 Open 时直接返回 []，不发起请求。"""
    client = _client_with(lambda request: httpx.Response(200, json=[]))
    with patch("app.core.infra.alertmanager.is_open", return_value=True):
        assert await client.alerts() == []


async def test_alerts_not_connected():
    """未连接（无客户端）返回 []。"""
    client = AlertmanagerClient()
    assert await client.alerts() == []


async def test_connect_without_url(monkeypatch):
    """未配置 ALERTMANAGER_URL 时 connect 后不可用。"""
    monkeypatch.setattr(am_module.settings, "ALERTMANAGER_URL", "")
    client = AlertmanagerClient()
    await client.connect()
    assert not client.is_connected
    assert await client.alerts() == []
