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

from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------- 工单回复（人工介入线程） ----------


@patch("app.api.ticket.list_replies", new_callable=AsyncMock)
@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_list_replies_owner_ok(mock_get, mock_list):
    """user 查自己工单的回复线程：200。"""
    mock_get.return_value = {"id": "TK-1", "user_id": "testuser"}
    mock_list.return_value = [
        {"id": 1, "sender_role": "user", "sender_username": "testuser", "content": "补充"}
    ]
    resp = client.get("/api/v1/ticket/TK-1/replies")
    assert resp.status_code == 200
    assert resp.json()["replies"][0]["content"] == "补充"


@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_list_replies_other_user_404(mock_get):
    """user 查他人工单回复：404（防枚举）。"""
    mock_get.return_value = {"id": "TK-1", "user_id": "someone-else"}
    resp = client.get("/api/v1/ticket/TK-1/replies")
    assert resp.status_code == 404


@patch("app.api.ticket.add_reply", new_callable=AsyncMock)
@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_add_reply_user_ok(mock_get, mock_add):
    """user 回复自己的工单：200，sender 透传身份。"""
    mock_get.return_value = {"id": "TK-1", "user_id": "testuser"}
    mock_add.return_value = {
        "id": 1,
        "ticket_id": "TK-1",
        "sender_role": "user",
        "sender_username": "testuser",
        "content": "补充订单号",
    }
    resp = client.post("/api/v1/ticket/TK-1/replies", json={"content": "补充订单号"})
    assert resp.status_code == 200
    mock_add.assert_awaited_once_with(
        ticket_id="TK-1", sender_role="user", sender_username="testuser", content="补充订单号"
    )


@patch("app.api.ticket.add_reply", new_callable=AsyncMock)
@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_add_reply_empty_content_422(mock_get, mock_add):
    """空内容：422（空白串能过 min_length，由 service strip 兜底；此处测零长度）。"""
    mock_get.return_value = {"id": "TK-1", "user_id": "testuser"}
    resp = client.post("/api/v1/ticket/TK-1/replies", json={"content": ""})
    assert resp.status_code == 422
    mock_add.assert_not_awaited()


@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_add_reply_missing_ticket_404(mock_get):
    """工单不存在：404。"""
    mock_get.return_value = None
    resp = client.post("/api/v1/ticket/TK-404/replies", json={"content": "hello"})
    assert resp.status_code == 404


# ---------- 客服回复审计（人工介入写操作追责链） ----------


async def fake_agent():
    return {"username": "agent1", "role": "agent"}


@patch("app.api.ticket.audit_service.record", new_callable=AsyncMock)
@patch("app.api.ticket.add_reply", new_callable=AsyncMock)
@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_add_reply_agent_writes_audit(mock_get, mock_add, mock_audit):
    """agent 回复触发人工介入：写审计（action=ticket.reply，含 prev_status）。"""
    mock_get.return_value = {"id": "TK-1", "user_id": "testuser", "status": "open"}
    mock_add.return_value = {"id": 1, "ticket_id": "TK-1", "sender_role": "agent"}
    original = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = fake_agent
    try:
        resp = client.post("/api/v1/ticket/TK-1/replies", json={"content": "已收到，处理中"})
    finally:
        app.dependency_overrides[get_current_user] = original
    assert resp.status_code == 200
    mock_audit.assert_awaited_once()
    args = mock_audit.await_args
    assert args.args[1] == "ticket.reply"
    assert args.args[2] == "ticket"
    assert args.args[3] == "TK-1"
    assert args.args[4]["prev_status"] == "open"


@patch("app.api.ticket.audit_service.record", new_callable=AsyncMock)
@patch("app.api.ticket.add_reply", new_callable=AsyncMock)
@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_add_reply_user_does_not_write_audit(mock_get, mock_add, mock_audit):
    """普通用户回复自己的工单：非客服侧写操作，不写审计。"""
    mock_get.return_value = {"id": "TK-1", "user_id": "testuser"}
    mock_add.return_value = {"id": 1, "ticket_id": "TK-1", "sender_role": "user"}
    resp = client.post("/api/v1/ticket/TK-1/replies", json={"content": "补充信息"})
    assert resp.status_code == 200
    mock_audit.assert_not_awaited()


# ---------- 转人工工单关联会话（conversation_id 落库全链） ----------

# 服务层（ticket_service）mock 工具：与 test_ticket_service.py 同模式，
# 就地复刻以保持本文件自包含（避免跨测试文件 import 耦合）


def _make_service_session() -> AsyncMock:
    """构造一个 mock AsyncSession。"""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _bind_service_session(mock_session_cls, session: AsyncMock) -> None:
    """将 mock session 绑定到 async_session() 上下文管理器。"""
    mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)


def _make_service_redis(connected: bool) -> MagicMock:
    """构造一个 mock RedisClient（connected=False 跳过幂等直接创建）。"""
    redis = MagicMock()
    redis.is_connected = connected
    redis.client = None
    return redis


@patch("app.core.ticket_service.async_session")
@patch("app.core.ticket_service.get_redis")
async def test_create_ticket_with_conversation_id_persists_and_returns_it(
    mock_redis, mock_session_cls
):
    """建单传入 conversation_id="C1"：落库到 Ticket 并在返回体中可见。"""
    from app.core.ticket_service import create_ticket as service_create_ticket

    session = _make_service_session()
    _bind_service_session(mock_session_cls, session)
    mock_redis.return_value = _make_service_redis(connected=False)

    result = await service_create_ticket(
        "转人工：需要人工", "描述", conversation_id="C1"
    )

    assert result["created"] is True
    assert result["ticket"]["conversation_id"] == "C1"
    persisted = session.add.call_args[0][0]
    assert persisted.conversation_id == "C1"


@patch("app.core.ticket_service.async_session")
@patch("app.core.ticket_service.get_redis")
async def test_create_ticket_without_conversation_id_defaults_empty(
    mock_redis, mock_session_cls
):
    """既有建单路径不回归：不传 conversation_id 时为空串（工单页手动建单）。"""
    from app.core.ticket_service import create_ticket as service_create_ticket

    session = _make_service_session()
    _bind_service_session(mock_session_cls, session)
    mock_redis.return_value = _make_service_redis(connected=False)

    result = await service_create_ticket("手动工单", "描述")

    assert result["created"] is True
    assert result["ticket"]["conversation_id"] == ""
    persisted = session.add.call_args[0][0]
    assert persisted.conversation_id == ""


@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_get_ticket_returns_conversation_id(mock_get):
    """GET /ticket/{id} 返回体含 conversation_id（转人工建单传入 C1 → 返回 C1）。"""
    mock_get.return_value = {
        "id": "TK-1",
        "title": "转人工",
        "user_id": "testuser",
        "conversation_id": "C1",
    }
    resp = client.get("/api/v1/ticket/TK-1")
    assert resp.status_code == 200
    assert resp.json()["conversation_id"] == "C1"


@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_get_ticket_conversation_id_empty_when_not_linked(mock_get):
    """未关联会话的旧工单：conversation_id 为空串（前端据此隐藏会话入口）。"""
    mock_get.return_value = {
        "id": "TK-old",
        "title": "旧工单",
        "user_id": "testuser",
        "conversation_id": "",
    }
    resp = client.get("/api/v1/ticket/TK-old")
    assert resp.status_code == 200
    assert resp.json()["conversation_id"] == ""


# ---------- 客服查看用户会话上下文（GET /{ticket_id}/conversation） ----------


@patch("app.api.ticket.list_messages", new_callable=AsyncMock)
@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_ticket_conversation_success(mock_get, mock_msgs):
    """agent 读关联工单：200，返回 conversation_id 与时间正序消息列表。

    list_messages 收到的 user_id 是工单归属者（会话主人），不是客服自己。
    """
    mock_get.return_value = {
        "id": "TK-1",
        "user_id": "customer1",
        "conversation_id": "C1",
    }
    mock_msgs.return_value = {
        "conversation_id": "C1",
        "messages": [
            {"role": "user", "content": "订单查不到", "intent": "task",
             "created_at": "2026-09-12T10:00:00"},
            {"role": "assistant", "content": "正在为您查询", "intent": "task",
             "created_at": "2026-09-12T10:00:05"},
        ],
    }

    async def agent_user():
        return {"username": "agent1", "role": "agent"}

    app.dependency_overrides[get_current_user] = agent_user
    try:
        resp = client.get("/api/v1/ticket/TK-1/conversation")
    finally:
        app.dependency_overrides[get_current_user] = fake_user

    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_id"] == "C1"
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    for m in data["messages"]:
        assert set(m) == {"role", "content", "created_at", "intent"}
    # 时间正序（created_at 单调不减）
    times = [m["created_at"] for m in data["messages"]]
    assert times == sorted(times)
    mock_msgs.assert_awaited_once_with("C1", user_id="customer1")


@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_ticket_conversation_ticket_missing_404(mock_get):
    """工单不存在：404（防枚举，与详情接口统一形状）。"""
    mock_get.return_value = None

    async def agent_user():
        return {"username": "agent1", "role": "agent"}

    app.dependency_overrides[get_current_user] = agent_user
    try:
        resp = client.get("/api/v1/ticket/TK-notexist/conversation")
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    assert resp.status_code == 404
    assert resp.json()["detail"] == "工单不存在"


@patch("app.api.ticket.list_messages", new_callable=AsyncMock)
@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_ticket_conversation_no_conversation_404(mock_get, mock_msgs):
    """工单存在但 conversation_id 为空：404「该工单未关联会话」，不调消息服务。"""
    mock_get.return_value = {
        "id": "TK-old",
        "user_id": "customer1",
        "conversation_id": "",
    }

    async def agent_user():
        return {"username": "agent1", "role": "agent"}

    app.dependency_overrides[get_current_user] = agent_user
    try:
        resp = client.get("/api/v1/ticket/TK-old/conversation")
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    assert resp.status_code == 404
    assert resp.json()["detail"] == "该工单未关联会话"
    mock_msgs.assert_not_awaited()


@patch("app.api.ticket.list_messages", new_callable=AsyncMock)
@patch("app.api.ticket.get_ticket", new_callable=AsyncMock)
def test_ticket_conversation_service_error_503(mock_get, mock_msgs):
    """消息服务异常（DB 不可用等）：503 降级，不抛 500 栈。"""
    mock_get.return_value = {
        "id": "TK-1",
        "user_id": "customer1",
        "conversation_id": "C1",
    }
    mock_msgs.side_effect = RuntimeError("db unavailable")

    async def agent_user():
        return {"username": "agent1", "role": "agent"}

    app.dependency_overrides[get_current_user] = agent_user
    try:
        resp = client.get("/api/v1/ticket/TK-1/conversation")
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    assert resp.status_code == 503


def test_ticket_conversation_user_role_403():
    """user 角色直调：require_staff 依赖挡下，403（不触发工单查询）。"""
    # autouse fixture 已将 get_current_user 覆盖为 user 角色
    resp = client.get("/api/v1/ticket/TK-1/conversation")
    assert resp.status_code == 403
