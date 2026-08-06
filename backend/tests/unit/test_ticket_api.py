"""工单 API 单元测试。

覆盖：
1. 创建工单成功
2. 创建工单验证失败（title 为空）-> 422
3. 列表查询成功
4. 状态流转成功
5. 非法状态流转（ValueError -> 400）
6. 权限不足（PermissionError -> 403）
7. 工单不存在（404）

mock 策略：mock app.api.ticket 中的 service 引用，不连真实 DB。
依赖 get_current_user 用 dependency_overrides 覆盖为 fake_user。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app

client = TestClient(app)


# 覆盖 get_current_user 依赖
async def fake_user():
    return {"username": "testuser", "role": "user"}


app.dependency_overrides[get_current_user] = fake_user


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
