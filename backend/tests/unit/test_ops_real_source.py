"""真实运维数据源（RealOpsDataSource）单元测试。

mock 策略：patch real_source 模块内绑定的客户端单例（get_prometheus 等），
验证响应映射、表达式替换、场景 UI 语义与失败降级，不连真实服务。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.ops.real_source import RealOpsDataSource

# 固定时间戳（2023-11-14T22:13:20Z），避免随 now 漂移
_TS = 1700000000


@pytest.fixture
def mock_clients(monkeypatch):
    """注入 mock 的三个基础设施客户端。"""
    prom = MagicMock()
    prom.connect = AsyncMock()
    prom.is_connected = True
    prom.query_range = AsyncMock(return_value=[{"ts": _TS, "value": 1.5}])
    prom.label_values = AsyncMock(return_value=["order-service", "api-gateway"])

    es = MagicMock()
    es.connect = AsyncMock()
    es.is_connected = True
    es.search = AsyncMock(return_value=[])

    am = MagicMock()
    am.connect = AsyncMock()
    am.is_connected = True
    am.alerts = AsyncMock(return_value=[])

    monkeypatch.setattr("app.core.ops.real_source.get_prometheus", lambda: prom)
    monkeypatch.setattr("app.core.ops.real_source.get_elasticsearch", lambda: es)
    monkeypatch.setattr("app.core.ops.real_source.get_alertmanager", lambda: am)
    return prom, es, am


async def test_query_metric_uses_expr_map(mock_clients):
    """query_metric 用外置表达式 + {service} 占位符替换。"""
    prom, es, am = mock_clients
    real = RealOpsDataSource()
    points = await real.query_metric("order-service", "error_rate", _TS - 3600, _TS)
    assert points == [{"ts": _TS, "value": 1.5}]
    expr = prom.query_range.call_args[0][0]
    assert expr == 'ops_error_rate{service="order-service"}'
    assert prom.query_range.call_args[0][1] == _TS - 3600  # start
    assert prom.query_range.call_args[0][2] == _TS  # end
    assert prom.query_range.call_args.kwargs["step"] == 60


async def test_query_metric_unknown_metric_empty(mock_clients):
    """未知指标返回空且不调用 Prometheus。"""
    prom, es, am = mock_clients
    real = RealOpsDataSource()
    assert await real.query_metric("order-service", "no_such_metric", 1, 100) == []
    prom.query_range.assert_not_awaited()


async def test_query_metric_prometheus_down_empty(mock_clients):
    """Prometheus 未连接时返回空。"""
    prom, es, am = mock_clients
    prom.is_connected = False
    real = RealOpsDataSource()
    assert await real.query_metric("order-service", "error_rate", 1, 100) == []


async def test_list_services_uses_label_values(mock_clients):
    """服务列表来自 label values。"""
    prom, es, am = mock_clients
    real = RealOpsDataSource()
    assert await real.list_services() == ["order-service", "api-gateway"]
    prom.label_values.assert_awaited_once()


async def test_list_metrics_from_expr_map():
    """指标列表来自外置表达式映射的键。"""
    real = RealOpsDataSource()
    metrics = await real.list_metrics("order-service")
    assert set(metrics) == {"cpu_usage", "memory_usage", "error_rate", "latency_p95", "qps"}


async def test_search_logs_builds_query_and_maps(mock_clients):
    """日志检索构造 ES 查询并按容错字段名映射。"""
    prom, es, am = mock_clients
    es.search = AsyncMock(
        return_value=[
            {
                "@timestamp": "2026-01-01T00:00:00Z",
                "service_name": "order-service",
                "log.level": "ERROR",
                "msg": "connection pool exhausted",
                "traceId": "abc123",
            }
        ]
    )
    real = RealOpsDataSource()
    logs = await real.search_logs(service="order-service", keyword="pool", limit=10)
    assert logs == [
        {
            "ts": 1767225600,  # 2026-01-01T00:00:00Z
            "service": "order-service",
            "level": "ERROR",
            "message": "connection pool exhausted",
            "trace_id": "abc123",
        }
    ]
    index, body = es.search.call_args[0]
    assert index == "logs-*"
    assert "query_string" in str(body)
    assert body["sort"] == [{"@timestamp": "desc"}]
    assert es.search.call_args.kwargs["size"] == 10


async def test_search_logs_es_down_empty(mock_clients):
    """ES 未连接时返回空。"""
    prom, es, am = mock_clients
    es.is_connected = False
    real = RealOpsDataSource()
    assert await real.search_logs() == []


async def test_query_changes_maps(mock_clients):
    """变更记录从 ES 变更索引映射。"""
    prom, es, am = mock_clients
    es.search = AsyncMock(
        return_value=[
            {
                "@timestamp": "2026-01-01T00:00:00Z",
                "service": "inventory-service",
                "change_type": "config",
                "content": "调大 max_pool_size",
            }
        ]
    )
    real = RealOpsDataSource()
    changes = await real.query_changes(limit=5)
    assert changes == [
        {
            "ts": 1767225600,
            "service": "inventory-service",
            "type": "config",
            "content": "调大 max_pool_size",
        }
    ]
    assert es.search.call_args[0][0] == "changes-*"


async def test_get_alerts_maps(mock_clients):
    """Alertmanager 原始告警映射为契约形状，含时间过滤。"""
    prom, es, am = mock_clients
    am.alerts = AsyncMock(
        return_value=[
            {
                "fingerprint": "fp1",
                "labels": {"alertname": "HighErrorRate", "service": "order-service", "severity": "critical"},
                "annotations": {"summary": "错误率超过阈值"},
                "startsAt": "2023-11-14T22:13:20Z",
            },
            {
                "labels": {"alertname": "DiskFull"},
                "annotations": {"description": "磁盘将满"},
                "startsAt": "2023-11-14T21:30:00Z",  # 窗口内（_TS 前 43 分钟）
            },
        ]
    )
    real = RealOpsDataSource()
    alerts = await real.get_alerts(start_ts=_TS - 3600, end_ts=_TS)
    assert alerts[0] == {
        "alert_id": "fp1",
        "service": "order-service",
        "metric": "HighErrorRate",
        "severity": "critical",
        "ts": _TS,
        "message": "错误率超过阈值",
    }
    # 无 fingerprint/labels.service/severity 时用兜底值
    assert alerts[1]["alert_id"] == "DiskFull"
    assert alerts[1]["service"] == "unknown"
    assert alerts[1]["severity"] == "warning"


async def test_get_alerts_time_filter_excludes(mock_clients):
    """超出时间窗的告警被过滤。"""
    prom, es, am = mock_clients
    am.alerts = AsyncMock(
        return_value=[
            {
                "labels": {"alertname": "OldAlert"},
                "startsAt": "2023-11-14T20:00:00Z",  # 早于窗口
            }
        ]
    )
    real = RealOpsDataSource()
    assert await real.get_alerts(start_ts=_TS - 3600, end_ts=_TS) == []


async def test_set_active_scenario_ui_semantics(mock_clients):
    """real 模式场景仅 UI 语义：设置只记录名字，get 返回 None。"""
    real = RealOpsDataSource()
    assert await real.set_active_scenario("conn_pool_exhausted") == "conn_pool_exhausted"
    assert await real.get_active_scenario() is None
    assert await real.set_active_scenario(None) is None
    with pytest.raises(ValueError):
        await real.set_active_scenario("not-exist")


async def test_list_scenarios_preset(mock_clients):
    """real 模式返回预置场景（UI 展示用）。"""
    real = RealOpsDataSource()
    names = {s["name"] for s in await real.list_scenarios()}
    assert names == {"conn_pool_exhausted", "slow_sql", "memory_leak"}


async def test_connect_probe_ok(monkeypatch):
    """健康探测通过 → 可用。"""
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

    real = RealOpsDataSource()
    await real.connect()
    assert real.is_available is True


async def test_connect_probe_fail(monkeypatch):
    """健康探测失败 → 不可用（auto 模式据此降级 mock）。"""
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

    real = RealOpsDataSource()
    await real.connect()
    assert real.is_available is False


async def test_connect_without_prometheus(monkeypatch):
    """Prometheus 未连接 → 不可用。"""
    prom = MagicMock()
    prom.connect = AsyncMock()
    prom.is_connected = False
    es = MagicMock()
    es.connect = AsyncMock()
    am = MagicMock()
    am.connect = AsyncMock()
    monkeypatch.setattr("app.core.ops.real_source.get_prometheus", lambda: prom)
    monkeypatch.setattr("app.core.ops.real_source.get_elasticsearch", lambda: es)
    monkeypatch.setattr("app.core.ops.real_source.get_alertmanager", lambda: am)

    real = RealOpsDataSource()
    await real.connect()
    assert real.is_available is False
