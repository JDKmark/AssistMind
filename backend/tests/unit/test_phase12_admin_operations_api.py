from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app

client = TestClient(app)


async def fake_admin():
    return {"username": "admin", "role": "admin"}


async def fake_user():
    return {"username": "alice", "role": "user"}


@pytest.fixture(autouse=True)
def auth_override():
    app.dependency_overrides[get_current_user] = fake_admin
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_admin_can_list_refunds_with_filters():
    result = {"refunds": [{"refund_id": "AF1", "status": "处理中"}], "total": 1}
    with patch("app.core.mall.data_source.list_refunds", new=AsyncMock(return_value=result)) as query:
        response = client.get(
            "/api/v1/mall/refunds?status=处理中&owner_username=alice&limit=10&offset=2"
        )

    assert response.status_code == 200
    assert response.json() == result
    query.assert_awaited_once_with(
        status="处理中", owner_username="alice", limit=10, offset=2
    )


def test_non_admin_cannot_list_refunds():
    app.dependency_overrides[get_current_user] = fake_user
    with patch("app.core.mall.data_source.list_refunds", new=AsyncMock()) as query:
        response = client.get("/api/v1/mall/refunds")

    assert response.status_code == 403
    query.assert_not_awaited()


def test_admin_status_update_records_audit():
    result = {"refund_id": "AF1", "status": "已通过"}
    with (
        patch("app.core.admin_service.update_refund_status", new=AsyncMock(return_value=result)) as update,
    ):
        response = client.patch("/api/v1/mall/refunds/AF1/status", json={"status": "已通过"})

    assert response.status_code == 200
    assert response.json() == result
    update.assert_awaited_once_with("AF1", "已通过", "admin")


def test_refund_terminal_transition_returns_400():
    with patch(
        "app.core.admin_service.update_refund_status",
        new=AsyncMock(side_effect=ValueError("非法状态流转")),
    ):
        response = client.patch("/api/v1/mall/refunds/AF1/status", json={"status": "已拒绝"})

    assert response.status_code == 400


def test_admin_overview_has_fixed_sections():
    result = {
        "orders": {"total": 0, "amount": 0, "by_status": {}},
        "tickets": {"total": 0, "by_status": {}},
        "refunds": {"total": 0, "by_status": {}},
        "users": {"total": 0, "by_role": {}},
        "feedback": {"total": 0, "negative": 0, "pending_export": 0},
        "knowledge": {"documents": 0, "chunks": 0},
        "degraded": [],
    }
    with patch("app.core.admin_service.get_overview", new=AsyncMock(return_value=result)) as overview:
        response = client.get("/api/v1/admin/overview")

    assert response.status_code == 200
    assert set(response.json()) == {"orders", "tickets", "refunds", "users", "feedback", "knowledge", "degraded"}
    overview.assert_awaited_once()


def test_admin_audit_query_forwards_filters():
    result = {"items": [{"action": "refund.status.update"}], "total": 1}
    with patch("app.core.audit_service.list_logs", new=AsyncMock(return_value=result)) as logs:
        response = client.get(
            "/api/v1/admin/audit?actor=admin&action=refund.status.update&target_type=refund&limit=5&offset=1"
        )

    assert response.status_code == 200
    logs.assert_awaited_once_with(
        actor="admin", action="refund.status.update", target_type="refund", limit=5, offset=1
    )
