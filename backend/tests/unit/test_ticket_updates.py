"""工单更新轮询单元测试（ticket_service.list_updates + API /ticket/updates）。

覆盖：
1. since 缺省（None）→ 空结果 + server_time，不触发 DB 查询
2. 非法 since → ValueError（API 层转 422）
3. 返回形状：id/title/status/priority/updated_at(ISO)/new_replies + server_time
4. user 角色隔离：SQL 过滤 tickets.user_id；agent/admin 不过滤
5. Z 后缀 / 带时区 ISO → 转 naive UTC 与 DB 列（TIMESTAMP WITHOUT TIME ZONE）一致
6. API 层：非法 since → 422 detail="since 参数无效"

mock 策略：mock async_session（参照 test_ticket_service.py 的 _make_session/_bind_session），
不连真实 DB；API 测试 mock service 函数 + dependency_overrides 覆盖鉴权。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.ticket_service import list_updates
from app.main import app
from app.models.ticket import Ticket


def _make_session() -> AsyncMock:
    """构造一个 mock AsyncSession。"""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _bind_session(mock_session_cls, session: AsyncMock) -> None:
    """将 mock session 绑定到 async_session() 上下文管理器。"""
    mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)


def _make_ticket(ticket_id: str, updated_at: datetime, user_id: str = "u1") -> Ticket:
    return Ticket(
        id=ticket_id,
        title=f"标题-{ticket_id}",
        description="d",
        priority="high",
        status="in_progress",
        user_id=user_id,
        updated_at=updated_at,
    )


def _bind_execute(session: AsyncMock, rows: list[tuple]) -> None:
    query_result = MagicMock()
    query_result.all = MagicMock(return_value=rows)
    session.execute = AsyncMock(return_value=query_result)


# ---------- 1. since 缺省：空结果 ----------


@patch("app.core.ticket_service.async_session")
async def test_list_updates_since_missing_returns_empty(mock_session_cls):
    """since 缺省：空结果 + server_time（首次轮询仅同步基线时间），不查询 DB。"""
    result = await list_updates(None, requester_role="user", requester_username="u1")

    assert result["tickets"] == []
    assert result["server_time"]  # 当前 UTC ISO 字符串
    mock_session_cls.assert_not_called()


# ---------- 2. 非法 since：ValueError ----------


@patch("app.core.ticket_service.async_session")
async def test_list_updates_invalid_since_raises_value_error(mock_session_cls):
    """非法 since 抛 ValueError（API 层转 422），不触发 DB 查询。"""
    with pytest.raises(ValueError):
        await list_updates("not-a-date", requester_role="user", requester_username="u1")
    mock_session_cls.assert_not_called()


# ---------- 3. 返回形状：new_replies + server_time ----------


@patch("app.core.ticket_service.async_session")
async def test_list_updates_returns_shape_with_new_replies(mock_session_cls):
    """返回形状：每单 id/title/status/priority/updated_at(ISO)/new_replies，附 server_time。"""
    session = _make_session()
    t1 = _make_ticket("TK-1", datetime(2026, 8, 28, 10, 0, 0))
    t2 = _make_ticket("TK-2", datetime(2026, 8, 28, 11, 0, 0))
    _bind_execute(session, [(t2, 2), (t1, 0)])
    _bind_session(mock_session_cls, session)

    result = await list_updates(
        "2026-08-28T00:00:00", requester_role="agent", requester_username="agent1"
    )

    assert result["server_time"]
    tickets = result["tickets"]
    assert [t["id"] for t in tickets] == ["TK-2", "TK-1"]  # 按服务层 SQL 倒序透传
    assert tickets[0]["title"] == "标题-TK-2"
    assert tickets[0]["status"] == "in_progress"
    assert tickets[0]["priority"] == "high"
    assert tickets[0]["updated_at"] == "2026-08-28T11:00:00"
    assert tickets[0]["new_replies"] == 2
    assert tickets[1]["new_replies"] == 0


@patch("app.core.ticket_service.async_session")
async def test_list_updates_sql_shape_or_condition_and_ordering(mock_session_cls):
    """SQL 形状：updated_at>since OR 存在新回复（ticket_replies join），按 updated_at 倒序。"""
    session = _make_session()
    _bind_execute(session, [])
    _bind_session(mock_session_cls, session)

    await list_updates(
        "2026-08-28T00:00:00", requester_role="agent", requester_username="agent1"
    )

    stmt = session.execute.await_args_list[0].args[0]
    sql = str(stmt)
    assert "ticket_replies" in sql  # 回复子查询参与条件/计数
    assert "OR" in sql.upper()  # updated_at > since OR 存在 > since 的回复
    assert "ORDER BY tickets.updated_at DESC" in sql


# ---------- 4. user 角色隔离 ----------


@patch("app.core.ticket_service.async_session")
async def test_list_updates_user_isolation_filters_own_tickets(mock_session_cls):
    """user 角色：SQL 过滤 tickets.user_id == 请求者；agent 角色不过滤。"""
    session = _make_session()
    _bind_execute(session, [])
    _bind_session(mock_session_cls, session)

    await list_updates(
        "2026-08-28T00:00:00", requester_role="user", requester_username="u1"
    )

    user_stmt = session.execute.await_args_list[0].args[0]
    # SELECT 列恒含 user_id，隔离断言只看 WHERE 子句
    assert "tickets.user_id" in str(user_stmt.whereclause)

    await list_updates(
        "2026-08-28T00:00:00", requester_role="agent", requester_username="agent1"
    )

    agent_stmt = session.execute.await_args_list[1].args[0]
    assert "tickets.user_id" not in str(agent_stmt.whereclause)

    # admin 同 agent：全量
    await list_updates(
        "2026-08-28T00:00:00", requester_role="admin", requester_username="root"
    )

    admin_stmt = session.execute.await_args_list[2].args[0]
    assert "tickets.user_id" not in str(admin_stmt.whereclause)


# ---------- 5. since 解析：Z 后缀 / 时区偏移 → naive UTC ----------


@patch("app.core.ticket_service.async_session")
async def test_list_updates_since_parsed_to_naive_utc(mock_session_cls):
    """Z 后缀与带时区偏移的 since 统一转 naive UTC 绑定参数（与 DB 列一致）。"""
    session = _make_session()
    _bind_execute(session, [])
    _bind_session(mock_session_cls, session)

    await list_updates(
        "2026-08-28T08:00:00+08:00", requester_role="agent", requester_username="a"
    )

    stmt = session.execute.await_args_list[0].args[0]
    dts = [v for v in stmt.compile().params.values() if isinstance(v, datetime)]
    assert dts, "since 应作为绑定参数参与比较"
    assert all(v.tzinfo is None for v in dts)
    assert all(v == datetime(2026, 8, 28, 0, 0, 0) for v in dts)

    # Z 后缀等价
    await list_updates(
        "2026-08-28T00:00:00Z", requester_role="agent", requester_username="a"
    )

    stmt_z = session.execute.await_args_list[1].args[0]
    dts_z = [v for v in stmt_z.compile().params.values() if isinstance(v, datetime)]
    assert dts_z
    assert all(v == datetime(2026, 8, 28, 0, 0, 0) for v in dts_z)


# ---------- 6. API 层：非法 since → 422 ----------


async def _fake_user():
    return {"username": "testuser", "role": "user"}


@pytest.fixture()
def _override_auth():
    """临时覆盖鉴权依赖为 user 用户，结束恢复（不跨文件污染）。"""
    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = _fake_user
    yield
    if original is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = original


@patch("app.api.ticket.list_updates", new_callable=AsyncMock)
def test_ticket_updates_api_invalid_since_returns_422(mock_list_updates, _override_auth):
    """API 层：list_updates 抛 ValueError → 422 detail="since 参数无效"。"""
    mock_list_updates.side_effect = ValueError("invalid")
    client = TestClient(app)
    resp = client.get("/api/v1/ticket/updates", params={"since": "bad-date"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "since 参数无效"


@patch("app.api.ticket.list_updates", new_callable=AsyncMock)
def test_ticket_updates_api_success_shape(mock_list_updates, _override_auth):
    """API 层：正常返回透传 service 结果，user 身份透传（username/role）。"""
    mock_list_updates.return_value = {
        "tickets": [{"id": "TK-1", "new_replies": 1}],
        "server_time": "2026-08-28T12:00:00",
    }
    client = TestClient(app)
    resp = client.get("/api/v1/ticket/updates", params={"since": "2026-08-28T00:00:00"})
    assert resp.status_code == 200
    assert resp.json()["tickets"][0]["new_replies"] == 1
    _, kwargs = mock_list_updates.call_args
    assert kwargs.get("requester_username") == "testuser"
    assert kwargs.get("requester_role") == "user"
