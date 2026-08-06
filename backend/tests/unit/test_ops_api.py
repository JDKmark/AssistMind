"""运维诊断 API 单元测试。

覆盖：
- 场景设置 / 列表
- 服务与指标查询
- 诊断 SSE 流程（mock 各环节 + 自动创建工单）
- 未认证访问拒绝
mock 策略：覆盖 get_current_user 依赖，mock 诊断各环节，不连真实外部服务。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app

client = TestClient(app)


async def fake_user():
    return {"username": "testuser", "role": "admin"}


app.dependency_overrides[get_current_user] = fake_user


def test_ops_scenario_set_and_list():
    """设置场景 + 列表场景。"""
    resp = client.get("/api/v1/ops/scenarios")
    assert resp.status_code == 200
    names = {s["name"] for s in resp.json()["scenarios"]}
    assert "conn_pool_exhausted" in names

    resp = client.post("/api/v1/ops/scenario", json={"name": "slow_sql"})
    assert resp.status_code == 200
    assert resp.json()["active_scenario"] == "slow_sql"

    resp = client.post("/api/v1/ops/scenario", json={"name": None})
    assert resp.json()["active_scenario"] is None


def test_ops_scenario_unknown_400():
    """未知场景返回 400。"""
    resp = client.post("/api/v1/ops/scenario", json={"name": "not-exist"})
    assert resp.status_code == 400


def test_ops_services_and_metrics():
    """服务与指标查询。"""
    resp = client.get("/api/v1/ops/services")
    assert resp.status_code == 200
    assert "order-service" in resp.json()["services"]
    assert resp.json()["source_mode"] == "mock"  # 单测强制 mock 模式

    resp = client.get("/api/v1/ops/metrics/order-service/error_rate")
    assert resp.status_code == 200
    assert len(resp.json()["points"]) > 0


def test_ops_diagnose_sse():
    """诊断 SSE 流程：start→planning→collecting→analyzing→done + 自动建单。"""
    report = {
        "summary": "连接池耗尽",
        "symptoms": ["错误率上升"],
        "root_cause": "inventory-service 连接池过小",
        "recovery": "调大连接池",
        "confidence": 0.9,
        "affected_services": ["order-service"],
        "evidence_summary": {"alerts": 1, "metrics": 3, "logs": 2, "changes": 1, "kb": 0},
    }
    with patch("app.api.ops._plan", new=AsyncMock(return_value={"services": ["order-service"], "data_sources": ["metrics"], "keywords": []})):
        with patch(
            "app.api.ops.collect",
            new=AsyncMock(
                return_value={
                    "evidence": {"alerts": [], "metrics": [], "logs": [], "changes": [], "kb": []},
                    "degraded": [],
                }
            ),
        ):
            with patch("app.api.ops.analyze", new=AsyncMock(return_value=report)):
                with patch(
                    "app.api.ops._create_ticket",
                    new=AsyncMock(
                        return_value={"ticket_id": "TK-TEST123", "created": True, "ticket": {"id": "TK-TEST123"}}
                    ),
                ):
                    with client.stream("POST", "/api/v1/ops/diagnose", json={"query": "订单服务故障"}) as resp:
                        assert resp.status_code == 200
                        body = resp.read().decode("utf-8")

    assert "event: start" in body
    assert "event: planning" in body
    assert "event: collecting" in body
    assert "event: analyzing" in body
    assert "event: done" in body
    assert "TK-TEST123" in body  # 自动创建故障工单
    assert "连接池耗尽" in body


def test_ops_diagnose_requires_auth():
    """未认证访问被拒绝（清除 override 后验证）。"""
    app.dependency_overrides.pop(get_current_user, None)
    try:
        resp = client.get("/api/v1/ops/scenarios")
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides[get_current_user] = fake_user
