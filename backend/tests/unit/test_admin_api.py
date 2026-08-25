from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.security.auth import create_access_token
from app.main import app

client = TestClient(app)
ADMIN_HEADERS = {
    "Authorization": f"Bearer {create_access_token({'sub': 'admin', 'role': 'admin'})}"
}
USER_HEADERS = {
    "Authorization": f"Bearer {create_access_token({'sub': 'alice', 'role': 'user'})}"
}


async def _list_users(**kwargs):
    return {
        "users": [
            {
                "id": "user-1",
                "username": "alice",
                "role": "user",
                "is_active": True,
                "created_at": "2026-08-25T00:00:00",
            }
        ],
        "total": 1,
    }


async def test_admin_can_list_users_without_password():
    with patch("app.core.admin_service.list_users", new=AsyncMock(side_effect=_list_users)):
        response = client.get("/api/v1/admin/users?role=user&keyword=ali", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["users"][0]["username"] == "alice"
    assert "hashed_password" not in response.json()["users"][0]


def test_non_admin_cannot_list_users():
    response = client.get("/api/v1/admin/users", headers=USER_HEADERS)

    assert response.status_code == 403


def test_admin_can_update_user_role_and_audit():
    result = {
        "id": "user-1",
        "username": "alice",
        "role": "agent",
        "is_active": True,
        "created_at": "2026-08-25T00:00:00",
    }
    with patch("app.core.admin_service.update_user", new=AsyncMock(return_value=result)) as update:
        response = client.patch(
            "/api/v1/admin/users/user-1",
            headers=ADMIN_HEADERS,
            json={"role": "agent"},
        )

    assert response.status_code == 200
    assert response.json()["role"] == "agent"
    update.assert_awaited_once()


def test_admin_role_grant_is_rejected():
    with patch(
        "app.core.admin_service.update_user",
        new=AsyncMock(side_effect=ValueError("角色只能为 user 或 agent")),
    ) as update:
        response = client.patch(
            "/api/v1/admin/users/user-1",
            headers=ADMIN_HEADERS,
            json={"role": "admin"},
        )

    assert response.status_code == 422
    update.assert_awaited_once()


def test_admin_target_cannot_be_modified():
    with patch(
        "app.core.admin_service.update_user",
        new=AsyncMock(side_effect=PermissionError("不能修改管理员账号")),
    ) as update:
        response = client.patch(
            "/api/v1/admin/users/admin-id",
            headers=ADMIN_HEADERS,
            json={"is_active": False},
        )

    assert response.status_code == 403
    update.assert_awaited_once()


def test_admin_update_requires_role_or_status():
    response = client.patch(
        "/api/v1/admin/users/user-1",
        headers=ADMIN_HEADERS,
        json={},
    )

    assert response.status_code == 422
