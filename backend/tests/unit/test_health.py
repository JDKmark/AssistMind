"""健康检查单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health_ok(client):
    """健康检查返回 ok。"""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["app"] == "AssistMind"
    # dependencies 必须返回实际探测结果（ok / degraded / disabled），不允许 pending 占位
    deps = data["dependencies"]
    assert set(deps) == {"qdrant", "redis", "postgres", "langfuse"}
    assert all(v in ("ok", "degraded", "disabled") for v in deps.values())


def test_health_reports_degraded_when_dependency_fails(client):
    """依赖探测失败时标记 degraded（不抛异常，健康检查不被依赖拖慢）。"""
    with (
        patch("app.api.health._probe_qdrant", AsyncMock(return_value="degraded")),
        patch("app.api.health._probe_redis", AsyncMock(return_value="degraded")),
        patch("app.api.health._probe_postgres", AsyncMock(return_value="disabled")),
    ):
        resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dependencies"]["qdrant"] == "degraded"
    assert data["dependencies"]["redis"] == "degraded"
    assert data["dependencies"]["postgres"] == "disabled"
    assert data["status"] == "ok"  # 依赖降级不影响应用状态


async def test_probe_qdrant_degraded_when_connect_fails():
    """Qdrant 连接失败时 _probe_qdrant 返回 degraded。"""
    from app.api import health

    class _FailingQdrant:
        is_connected = False

        async def connect(self):
            self.is_connected = False  # connect 后仍不可用

    with patch("app.api.health.get_qdrant", return_value=_FailingQdrant()):
        result = await health._probe_qdrant()
    assert result == "degraded"
