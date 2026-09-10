"""工单 API 单元测试。

覆盖：
1. 创建工单成功
2. 创建工单验证失败（title 为空）-> 422
3. 列表查询成功
4. 状态流转成功
5. 非法状态流转（ValueError -> 400）
6. 权限不足（PermissionError -> 403）
7. 工单不存在（404）
8. 工单详情归属隔离（user 查他人工单 404 / agent 查他人工单 200）
9. user 视角正向用例（查自己工单 200 / 列表仅自己 user_id 过滤 / agent 列表不受过滤）

mock 策略：mock app.api.ticket 中的 service 引用，不连真实 DB。
依赖 get_current_user 用 dependency_overrides 覆盖为 fake_user（autouse fixture，
结束后恢复原值，不跨文件污染其他测试的鉴权接口）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app

client = TestClient(app)


# 覆盖 get_current_user 依赖
async def fake_user():
    return {"username": "testuser", "role": "user"}


@pytest.fixture(autouse=True)
def _override_auth():
    """每个测试临时用 user 用户覆盖鉴权依赖，结束后恢复原值（不跨文件污染）。"""
    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = fake_user
    yield
    if original is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = original


# ---------- 1. 创建工单成功 ----------


@patch("app.api.ticket.create_ticket", new_callable=AsyncMock)
def test_create_ticket_success(mock_create):
    """创建工单成功：mock create_ticket 返回 created=True，POST / 验证 200。"""
    mock_create.return_value = {
        "ticket_id": "TK-123",
        "created": True,
        "ticket": {"id": "TK-123", "title": "测试工单"},
    }
    resp = client.post(
        "/api/v1/ticket/",
        json={"title": "测试工单", "description": "问题描述", "priority": "normal"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket_id"] == "TK-123"
    assert data["created"] is True
    mock_create.assert_awaited_once()
    # 验证 user_id 透传
    _, kwargs = mock_create.call_args
    assert kwargs.get("user_id") == "testuser"


# ---------- 2. 创建工单验证失败 ----------


@patch("app.api.ticket.create_ticket", new_callable=AsyncMock)
def test_create_ticket_validation_failed(mock_create):
    """title 为空：Pydantic min_length=1 校验返回 422，create_ticket 不应被调用。"""
    resp = client.post(
        "/api/v1/ticket/",
        json={"title": "", "description": "问题描述"},
    )
    assert resp.status_code == 422
    mock_create.assert_not_awaited()


# ---------- 3. 列表查询 ----------


@patch("app.api.ticket.list_tickets", new_callable=AsyncMock)
def test_list_tickets_success(mock_list):
    """列表查询成功：返回 tickets 与 total。"""
    mock_list.return_value = {
        "tickets": [{"id": "TK-1"}, {"id": "TK-2"}],
        "total": 2,
    }
    resp = client.get("/api/v1/ticket/list")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["tickets"]) == 2


# ---------- 4. 状态流转成功 ----------


@patch("app.api.ticket.update_status", new_callable=AsyncMock)
def test_update_status_success(mock_update):
    """状态流转成功：返回 {ticket_id, status}，验证 200。"""
    mock_update.return_value = {"ticket_id": "TK-1", "status": "in_progress"}
    resp = client.patch(
        "/api/v1/ticket/TK-1/status",
        json={"status": "in_progress"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket_id"] == "TK-1"
    assert data["status"] == "in_progress"


# ---------- 5. 非法状态流转 ----------


@patch("app.api.ticket.update_status", new_callable=AsyncMock)
def test_update_status_illegal_transition(mock_update):
    """非法状态流转：ValueError -> 400。"""
    mock_update.side_effect = ValueError("非法状态流转: closed -> open")
    resp = client.patch(
        "/api/v1/ticket/TK-1/status",
        json={"status": "open"},
    )
    assert resp.status_code == 400
    assert "非法状态流转" in resp.json()["detail"]


# ---------- 6. 权限不足 ----------


@patch("app.api.ticket.update_status", new_callable=AsyncMock)
def test_update_status_permission_denied(mock_update):
    """权限不足：PermissionError -> 403。"""
    mock_update.side_effect = PermissionError("角色 user 无权执行流转 open->in_progress")
    resp = client.patch(
        "/api/v1/ticket/TK-1/status",
        json={"status": "in_progress"},
    )
    assert resp.status_code == 403


# ---------- 7. 工单不存在 ----------


@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_get_ticket_not_found(mock_get):
    """工单不存在：get_ticket 返回 None -> 404。"""
    mock_get.return_value = None
    resp = client.get("/api/v1/ticket/TK-notexist")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "工单不存在"


# ---------- 8. 工单详情归属隔离 ----------


@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_get_ticket_user_cannot_view_others_ticket(mock_get):
    """user 查他人工单：404（与不存在的工单统一形状，防枚举）。"""
    mock_get.return_value = {"id": "TK-2", "title": "他人工单", "user_id": "user2"}

    async def user1():
        return {"username": "user1", "role": "user"}

    app.dependency_overrides[get_current_user] = user1
    try:
        resp = client.get("/api/v1/ticket/TK-2")
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    assert resp.status_code == 404
    assert resp.json()["detail"] == "工单不存在"


@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_get_ticket_agent_can_view_others_ticket(mock_get):
    """agent 查他人工单：200，不受归属限制。"""
    mock_get.return_value = {"id": "TK-2", "title": "他人工单", "user_id": "user2"}

    async def agent_user():
        return {"username": "agent1", "role": "agent"}

    app.dependency_overrides[get_current_user] = agent_user
    try:
        resp = client.get("/api/v1/ticket/TK-2")
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    assert resp.status_code == 200
    assert resp.json()["id"] == "TK-2"


# ---------- 9. user 视角正向用例 ----------


@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_get_ticket_user_can_view_own_ticket(mock_get):
    """user 查自己的工单：200（归属隔离的正向场景）。"""
    mock_get.return_value = {"id": "TK-1", "title": "我的工单", "user_id": "user1"}

    async def user1():
        return {"username": "user1", "role": "user"}

    app.dependency_overrides[get_current_user] = user1
    try:
        resp = client.get("/api/v1/ticket/TK-1")
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    assert resp.status_code == 200
    assert resp.json()["id"] == "TK-1"
    assert resp.json()["user_id"] == "user1"


@patch("app.api.ticket.list_tickets", new_callable=AsyncMock)
def test_list_tickets_user_filtered_by_own_username(mock_list):
    """user 角色列表查询：service 应收到 user_id=自己（只见自己的工单）。"""
    mock_list.return_value = {"tickets": [], "total": 0}
    resp = client.get("/api/v1/ticket/list")
    assert resp.status_code == 200
    _, kwargs = mock_list.call_args
    assert kwargs.get("user_id") == "testuser"


@patch("app.api.ticket.list_tickets", new_callable=AsyncMock)
def test_list_tickets_agent_not_filtered(mock_list):
    """agent 角色列表查询：user_id=None（可见全部工单）。"""
    mock_list.return_value = {"tickets": [], "total": 0}

    async def agent_user():
        return {"username": "agent1", "role": "agent"}

    app.dependency_overrides[get_current_user] = agent_user
    try:
        resp = client.get("/api/v1/ticket/list")
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    assert resp.status_code == 200
    _, kwargs = mock_list.call_args
    assert kwargs.get("user_id") is None


# ---------- 10. 状态流转透传当前用户名 ----------


@patch("app.api.ticket.update_status", new_callable=AsyncMock)
def test_update_status_passes_user_id(mock_update):
    """API 层向 update_status 透传 user_id=当前用户名（归属校验依据）。"""
    mock_update.return_value = {"ticket_id": "TK-1", "status": "closed"}
    resp = client.patch(
        "/api/v1/ticket/TK-1/status",
        json={"status": "closed"},
    )
    assert resp.status_code == 200
    mock_update.assert_awaited_once()
    _, kwargs = mock_update.call_args
    assert kwargs.get("user_id") == "testuser"
    assert kwargs.get("user_role") == "user"

@patch("app.api.ticket.list_tickets", new_callable=AsyncMock)
def test_admin_list_tickets_forwards_priority_and_customer_filters(mock_list):
    mock_list.return_value = {"tickets": [], "total": 0}

    async def admin_user():
        return {"username": "admin", "role": "admin"}

    app.dependency_overrides[get_current_user] = admin_user
    try:
        resp = client.get(
            "/api/v1/ticket/list?status=open&priority=urgent&user_id=alice&limit=20&offset=5"
        )
    finally:
        app.dependency_overrides[get_current_user] = fake_user

    assert resp.status_code == 200
    mock_list.assert_awaited_once_with(
        status="open", user_id="alice", priority="urgent", limit=20, offset=5
    )
