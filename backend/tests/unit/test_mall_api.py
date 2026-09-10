"""商城管理 API 单元测试（GET /api/v1/mall/orders）。

覆盖：
1. 无 token → 401（真实鉴权依赖，不覆盖 get_current_user）
2. user 角色 → 403（require_admin）
3. admin → 200 返回订单列表
4. 过滤/分页参数透传到 mall 数据源门面
5. 数据源降级（degraded 标记）不 500

mock 策略：monkeypatch app.core.mall.data_source.list_orders（门面），
不连真实 DB；依赖 get_current_user 用 dependency_overrides 覆盖
（autouse fixture 默认 user，结束后恢复原值，不跨文件污染）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.mall import data_source as mall_ds
from app.main import app

client = TestClient(app)


# 覆盖 get_current_user 依赖
async def fake_user():
    return {"username": "user1", "role": "user"}


async def fake_admin():
    return {"username": "admin", "role": "admin"}


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


# ---------- 1. 无 token → 401 ----------


def test_list_orders_no_token_401(monkeypatch):
    """无 Authorization 头：OAuth2PasswordBearer 直接 401，不触达数据源。"""
    mock_list = AsyncMock(return_value={"orders": [], "total": 0})
    monkeypatch.setattr(mall_ds, "list_orders", mock_list)
    app.dependency_overrides.pop(get_current_user, None)
    try:
        resp = client.get("/api/v1/mall/orders")
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    assert resp.status_code == 401
    mock_list.assert_not_awaited()


# ---------- 2. user 角色 → 403 ----------


def test_list_orders_user_role_403(monkeypatch):
    """user 角色被 require_admin 拒绝：403，数据源不被调用。"""
    mock_list = AsyncMock(return_value={"orders": [], "total": 0})
    monkeypatch.setattr(mall_ds, "list_orders", mock_list)
    resp = client.get("/api/v1/mall/orders")
    assert resp.status_code == 403
    mock_list.assert_not_awaited()


# ---------- 3. admin 返回列表 ----------


def test_list_orders_admin_returns_list(monkeypatch):
    """admin：返回数据源 dict（orders + total）。"""
    mock_list = AsyncMock(
        return_value={
            "orders": [
                {
                    "order_sn": "20240801001",
                    "owner_username": "user1",
                    "status": "已发货",
                    "pay_amount": 6999,
                    "logistics_no": "SF1234567890",
                    "created_at": "2024-08-01 09:30:00",
                }
            ],
            "total": 1,
        }
    )
    monkeypatch.setattr(mall_ds, "list_orders", mock_list)
    app.dependency_overrides[get_current_user] = fake_admin
    try:
        resp = client.get("/api/v1/mall/orders")
    finally:
        app.dependency_overrides[get_current_user] = fake_user

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["orders"][0]["order_sn"] == "20240801001"
    assert data["orders"][0]["owner_username"] == "user1"


# ---------- 4. 过滤/分页参数透传 ----------


def test_list_orders_admin_filters_pass_through(monkeypatch):
    """admin：owner_username/status/limit/offset 透传到门面 list_orders。"""
    mock_list = AsyncMock(return_value={"orders": [], "total": 0})
    monkeypatch.setattr(mall_ds, "list_orders", mock_list)
    app.dependency_overrides[get_current_user] = fake_admin
    try:
        resp = client.get(
            "/api/v1/mall/orders?owner_username=user1&status=已发货&limit=20&offset=10"
        )
    finally:
        app.dependency_overrides[get_current_user] = fake_user

    assert resp.status_code == 200
    mock_list.assert_awaited_once()
    _, kwargs = mock_list.call_args
    assert kwargs["owner_username"] == "user1"
    assert kwargs["status"] == "已发货"
    assert kwargs["limit"] == 20
    assert kwargs["offset"] == 10


def test_list_orders_rejects_invalid_pagination(monkeypatch):
    """limit=0 / limit=201 / offset=-1：Query 校验 422，不触达数据源。"""
    mock_list = AsyncMock(return_value={"orders": [], "total": 0})
    monkeypatch.setattr(mall_ds, "list_orders", mock_list)
    app.dependency_overrides[get_current_user] = fake_admin
    try:
        assert client.get("/api/v1/mall/orders?limit=0").status_code == 422
        assert client.get("/api/v1/mall/orders?limit=201").status_code == 422
        assert client.get("/api/v1/mall/orders?offset=-1").status_code == 422
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    mock_list.assert_not_awaited()


# ---------- 5. 数据源降级不 500 ----------


def test_list_orders_degraded_source_not_500(monkeypatch):
    """数据源降级（degraded=["postgres"]）：仍 200，响应带 degraded 标记。"""
    mock_list = AsyncMock(
        return_value={"orders": [], "total": 0, "degraded": ["postgres"]}
    )
    monkeypatch.setattr(mall_ds, "list_orders", mock_list)
    app.dependency_overrides[get_current_user] = fake_admin
    try:
        resp = client.get("/api/v1/mall/orders")
    finally:
        app.dependency_overrides[get_current_user] = fake_user

    assert resp.status_code == 200
    data = resp.json()
    assert data["orders"] == []
    assert data["total"] == 0
    assert data["degraded"] == ["postgres"]


# ---------- 6. my-orders 用户订单列表 ----------


def test_my_orders_user_returns_own_orders(monkeypatch):
    """user JWT：200，requester_username 透传为当前用户名，响应含 items 明细。"""
    mock_my = AsyncMock(
        return_value={
            "orders": [
                {
                    "order_sn": "20240801001",
                    "status": "已发货",
                    "pay_amount": 6999,
                    "logistics_no": "SF1234567890",
                    "created_at": "2024-08-01 09:30:00",
                    "items": [
                        {
                            "product_id": "P001",
                            "name": "华为 Mate 60 Pro",
                            "spec": "256G 雅丹黑",
                            "price": 6999,
                            "quantity": 1,
                        }
                    ],
                }
            ],
            "total": 1,
        }
    )
    monkeypatch.setattr(mall_ds, "my_orders", mock_my)
    resp = client.get("/api/v1/mall/my-orders")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["orders"][0]["order_sn"] == "20240801001"
    assert data["orders"][0]["items"][0]["name"] == "华为 Mate 60 Pro"
    mock_my.assert_awaited_once()
    _, kwargs = mock_my.call_args
    assert kwargs["requester_username"] == "user1"


def test_my_orders_no_token_401(monkeypatch):
    """无 Authorization 头：401，不触达数据源。"""
    mock_my = AsyncMock(return_value={"orders": [], "total": 0})
    monkeypatch.setattr(mall_ds, "my_orders", mock_my)
    app.dependency_overrides.pop(get_current_user, None)
    try:
        resp = client.get("/api/v1/mall/my-orders")
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    assert resp.status_code == 401
    mock_my.assert_not_awaited()


def test_my_orders_ignores_client_owner_param(monkeypatch):
    """客户端传 owner_username 不影响结果：不透传给数据源（归属服务端强制）。"""
    mock_my = AsyncMock(return_value={"orders": [], "total": 0})
    monkeypatch.setattr(mall_ds, "my_orders", mock_my)
    resp = client.get(
        "/api/v1/mall/my-orders?owner_username=other&status=已发货&limit=5&offset=1"
    )

    assert resp.status_code == 200
    mock_my.assert_awaited_once()
    _, kwargs = mock_my.call_args
    assert kwargs["requester_username"] == "user1"
    assert "owner_username" not in kwargs
    assert kwargs["status"] == "已发货"
    assert kwargs["limit"] == 5
    assert kwargs["offset"] == 1
