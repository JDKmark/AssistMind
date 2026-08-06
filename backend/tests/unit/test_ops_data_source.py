"""运维数据源单元测试（mock 模式，经门面调用）。

覆盖：
- 场景设置 / 恢复（无故障基线）
- query_metric：故障窗异常形态 / 非法服务
- search_logs：服务与关键字过滤
- query_changes / get_alerts
- 门面数据源切换逻辑见 test_ops_data_source_gateway.py
"""

from __future__ import annotations

from app.core.ops import data_source as ds


async def test_set_and_clear_scenario():
    """设置/清除活动场景。"""
    assert await ds.set_active_scenario("conn_pool_exhausted") == "conn_pool_exhausted"
    assert (await ds.get_active_scenario()).name == "conn_pool_exhausted"
    assert await ds.set_active_scenario(None) is None
    assert await ds.get_active_scenario() is None


async def test_set_unknown_scenario_raises():
    """未知场景抛 ValueError。"""
    try:
        await ds.set_active_scenario("not-exist")
        assert False, "应抛 ValueError"
    except ValueError:
        pass


async def test_query_metric_normal_baseline():
    """无故障场景时返回基线数据（无异常尖峰）。"""
    await ds.set_active_scenario(None)
    pts = await ds.query_metric("order-service", "error_rate")
    assert len(pts) > 0
    assert all(p["value"] < 1.0 for p in pts)


async def test_query_metric_anomaly_in_window():
    """故障窗内 error_rate 应出现异常峰值。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    pts = await ds.query_metric("order-service", "error_rate")
    assert len(pts) > 0
    assert max(p["value"] for p in pts) > 5.0


async def test_query_metric_invalid_service():
    """非法服务返回空列表。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    assert await ds.query_metric("not-exist", "error_rate") == []


async def test_search_logs_filter():
    """日志按服务/关键字过滤。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    logs = await ds.search_logs(service="order-service")
    assert all(lg["service"] == "order-service" for lg in logs)
    logs_kw = await ds.search_logs(keyword="connection")
    assert all("connection" in lg["message"].lower() for lg in logs_kw)


async def test_search_logs_empty_without_scenario():
    """无活动场景时无异常日志。"""
    await ds.set_active_scenario(None)
    assert await ds.search_logs() == []


async def test_query_changes():
    """变更记录含连接池配置变更。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    changes = await ds.query_changes()
    assert len(changes) >= 1
    assert changes[0]["service"] == "inventory-service"
    assert "max_pool_size" in changes[0]["content"]


async def test_get_alerts():
    """告警列表含 critical 告警。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    alerts = await ds.get_alerts()
    assert len(alerts) >= 2
    assert any(a["severity"] == "critical" for a in alerts)


async def test_list_scenarios():
    """预置 3 个场景。"""
    scenarios = await ds.list_scenarios()
    assert {s["name"] for s in scenarios} == {"conn_pool_exhausted", "slow_sql", "memory_leak"}


async def test_list_hosts():
    """mock 模式按服务返回预置主机拓扑，未知服务返回空。"""
    hosts = await ds.list_hosts("order-service")
    assert len(hosts) >= 2
    assert all(h.startswith("order-") for h in hosts)
    assert await ds.list_hosts("not-exist") == []
