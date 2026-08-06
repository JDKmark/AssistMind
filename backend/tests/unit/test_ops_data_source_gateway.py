"""运维数据源门面切换测试：OPS_DATA_SOURCE = mock | real | auto。

注意：conftest 的 force_mock_ops_source autouse fixture 默认强制 mock，
本文件用例通过重新 monkeypatch settings 覆盖模式选择。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.config import Settings
from app.core.ops import data_source


def _set_mode(monkeypatch, **kwargs) -> None:
    monkeypatch.setattr(data_source, "settings", Settings(**kwargs))
    data_source.reset_source()


async def test_mock_mode_uses_mock(monkeypatch):
    """mock 模式恒用模拟数据源（即使配置了 PROMETHEUS_URL）。"""
    _set_mode(monkeypatch, OPS_DATA_SOURCE="mock", PROMETHEUS_URL="http://prom:9090")
    assert await data_source.get_source_mode() == "mock"
    services = await data_source.list_services()
    assert "order-service" in services  # mock 预置服务


async def test_real_mode_uses_real(monkeypatch):
    """real 模式恒用真实数据源（不做探测）。"""
    _set_mode(monkeypatch, OPS_DATA_SOURCE="real", PROMETHEUS_URL="http://prom:9090")
    assert await data_source.get_source_mode() == "real"


async def test_auto_without_url_uses_mock(monkeypatch):
    """auto 模式未配置 PROMETHEUS_URL → mock。"""
    _set_mode(monkeypatch, OPS_DATA_SOURCE="auto", PROMETHEUS_URL="")
    assert await data_source.get_source_mode() == "mock"
    assert await data_source.get_active_scenario() is None  # mock 无场景时 None


async def test_auto_with_url_probe_ok_uses_real(monkeypatch):
    """auto 模式配置了 URL 且健康探测通过 → real。"""
    prom = MagicMock()
    prom.connect = AsyncMock()
    prom.is_connected = True
    prom.ping = AsyncMock(return_value=True)
    es = MagicMock()
    es.connect = AsyncMock()
    am = MagicMock()
    am.connect = AsyncMock()
    monkeypatch.setattr("app.core.ops.real_source.get_prometheus", lambda: prom)
    monkeypatch.setattr("app.core.ops.real_source.get_elasticsearch", lambda: es)
    monkeypatch.setattr("app.core.ops.real_source.get_alertmanager", lambda: am)

    _set_mode(monkeypatch, OPS_DATA_SOURCE="auto", PROMETHEUS_URL="http://prom:9090")
    assert await data_source.get_source_mode() == "real"


async def test_auto_with_url_probe_fail_falls_back_mock(monkeypatch):
    """auto 模式健康探测失败 → 降级 mock。"""
    prom = MagicMock()
    prom.connect = AsyncMock()
    prom.is_connected = True
    prom.ping = AsyncMock(return_value=False)
    es = MagicMock()
    es.connect = AsyncMock()
    am = MagicMock()
    am.connect = AsyncMock()
    monkeypatch.setattr("app.core.ops.real_source.get_prometheus", lambda: prom)
    monkeypatch.setattr("app.core.ops.real_source.get_elasticsearch", lambda: es)
    monkeypatch.setattr("app.core.ops.real_source.get_alertmanager", lambda: am)

    _set_mode(monkeypatch, OPS_DATA_SOURCE="auto", PROMETHEUS_URL="http://prom:9090")
    assert await data_source.get_source_mode() == "mock"


async def test_auto_with_url_not_connected_falls_back_mock(monkeypatch):
    """auto 模式 Prometheus 未连接 → 降级 mock。"""
    prom = MagicMock()
    prom.connect = AsyncMock()
    prom.is_connected = False
    monkeypatch.setattr("app.core.ops.real_source.get_prometheus", lambda: prom)

    _set_mode(monkeypatch, OPS_DATA_SOURCE="auto", PROMETHEUS_URL="http://prom:9090")
    assert await data_source.get_source_mode() == "mock"
