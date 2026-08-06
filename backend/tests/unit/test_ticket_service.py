"""工单服务单元测试。

覆盖：
1. 正常创建工单（Redis 不可用，跳过幂等）
2. 幂等创建：锁获取成功 + 查重命中已存在工单
3. 锁获取失败：直接查重返回已存在
4. 状态正向流转：open→in_progress（agent）
5. 非法状态流转：closed→open 抛 ValueError
6. 权限不足：user 角色流转抛 PermissionError
7. Redis 不可用降级：仍能创建
8. list_tickets 过滤：参数传递 + 返回结构

mock 策略：mock async_session 与 get_redis，不连真实 DB/Redis。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.ticket_service import (
    create_ticket,
    list_tickets,
    update_status,
)
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


def _make_redis(connected: bool, lock_result=True) -> MagicMock:
    """构造一个 mock RedisClient。

    connected=False 表示 Redis 不可用；connected=True 时 client.set 返回 lock_result。
    """
    redis = MagicMock()
    redis.is_connected = connected
    client = MagicMock()
    client.set = AsyncMock(return_value=lock_result)
    client.delete = AsyncMock(return_value=1)
    redis.client = client
    return redis


def _make_existing_ticket() -> Ticket:
    """构造一个已存在的工单对象（用于查重命中场景）。"""
    return Ticket(
        id="TK-existing",
        title="测试标题",
        description="测试描述",
        priority="normal",
        status="open",
        category="",
        user_id="system",
    )


# ---------- 1. 正常创建 ----------


@patch("app.core.ticket_service.async_session")
@patch("app.core.ticket_service.get_redis")
async def test_create_ticket_normal(mock_redis, mock_session_cls):
    """正常创建：Redis 不可用，跳过幂等，验证返回 ticket_id 和 created=True。"""
    session = _make_session()
    _bind_session(mock_session_cls, session)
    mock_redis.return_value = _make_redis(connected=False)

    result = await create_ticket("测试标题", "测试描述")

    assert result["created"] is True
    assert result["ticket_id"].startswith("TK-")
    assert result["ticket"]["title"] == "测试标题"
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


# ---------- 2. 幂等创建：锁获取成功 + 查重命中 ----------


@patch("app.core.ticket_service.async_session")
@patch("app.core.ticket_service.get_redis")
async def test_create_ticket_idempotent_existing(mock_redis, mock_session_cls):
    """幂等：锁获取成功（SET NX 返回 True），查重命中已存在工单，created=False。"""
    session = _make_session()
    existing = _make_existing_ticket()
    query_result = MagicMock()
    query_result.scalar_one_or_none = MagicMock(return_value=existing)
    session.execute = AsyncMock(return_value=query_result)
    _bind_session(mock_session_cls, session)
    mock_redis.return_value = _make_redis(connected=True, lock_result=True)

    result = await create_ticket("测试标题", "测试描述")

    assert result["created"] is False
    assert result["ticket_id"] == "TK-existing"
    assert result["ticket"]["id"] == "TK-existing"
    # 命中幂等，不应执行创建
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


# ---------- 3. 锁获取失败：直接查重返回 ----------


@patch("app.core.ticket_service.async_session")
@patch("app.core.ticket_service.get_redis")
async def test_create_ticket_lock_failed_returns_existing(mock_redis, mock_session_cls):
    """锁获取失败（SET NX 返回 None），直接查重返回已存在工单。"""
    session = _make_session()
    existing = _make_existing_ticket()
    query_result = MagicMock()
    query_result.scalar_one_or_none = MagicMock(return_value=existing)
    session.execute = AsyncMock(return_value=query_result)
    _bind_session(mock_session_cls, session)
    # lock_result=None 表示锁被其他请求持有
    mock_redis.return_value = _make_redis(connected=True, lock_result=None)

    result = await create_ticket("测试标题", "测试描述")

    assert result["created"] is False
    assert result["ticket_id"] == "TK-existing"
    session.add.assert_not_called()


# ---------- 4. 状态正向流转 ----------


@patch("app.core.ticket_service.async_session")
@patch("app.core.ticket_service.get_redis")
async def test_update_status_open_to_in_progress(mock_redis, mock_session_cls):
    """open→in_progress 流转成功（role=agent）。"""
    session = _make_session()
    ticket = Ticket(
        id="TK-flow",
        title="t",
        description="d",
        priority="normal",
        status="open",
        user_id="system",
    )
    query_result = MagicMock()
    query_result.scalar_one_or_none = MagicMock(return_value=ticket)
    session.execute = AsyncMock(return_value=query_result)
    _bind_session(mock_session_cls, session)
    mock_redis.return_value = _make_redis(connected=False)

    result = await update_status("TK-flow", "in_progress", user_role="agent")

    assert result == {"ticket_id": "TK-flow", "status": "in_progress"}
    assert ticket.status == "in_progress"
    session.commit.assert_awaited_once()


# ---------- 5. 非法状态流转 ----------


@patch("app.core.ticket_service.async_session")
@patch("app.core.ticket_service.get_redis")
async def test_update_status_illegal_transition_raises(mock_redis, mock_session_cls):
    """closed→open 为非法流转，抛 ValueError。"""
    session = _make_session()
    ticket = Ticket(
        id="TK-closed",
        title="t",
        description="d",
        priority="normal",
        status="closed",
        user_id="system",
    )
    query_result = MagicMock()
    query_result.scalar_one_or_none = MagicMock(return_value=ticket)
    session.execute = AsyncMock(return_value=query_result)
    _bind_session(mock_session_cls, session)
    mock_redis.return_value = _make_redis(connected=False)

    with pytest.raises(ValueError):
        await update_status("TK-closed", "open", user_role="admin")

    # 非法流转不应提交
    session.commit.assert_not_awaited()


# ---------- 6. 权限不足 ----------


@patch("app.core.ticket_service.async_session")
@patch("app.core.ticket_service.get_redis")
async def test_update_status_permission_denied(mock_redis, mock_session_cls):
    """user 角色尝试流转 open→in_progress 抛 PermissionError。"""
    session = _make_session()
    ticket = Ticket(
        id="TK-perm",
        title="t",
        description="d",
        priority="normal",
        status="open",
        user_id="system",
    )
    query_result = MagicMock()
    query_result.scalar_one_or_none = MagicMock(return_value=ticket)
    session.execute = AsyncMock(return_value=query_result)
    _bind_session(mock_session_cls, session)
    mock_redis.return_value = _make_redis(connected=False)

    with pytest.raises(PermissionError):
        await update_status("TK-perm", "in_progress", user_role="user")

    session.commit.assert_not_awaited()


# ---------- 7. Redis 不可用降级 ----------


@patch("app.core.ticket_service.async_session")
@patch("app.core.ticket_service.get_redis")
async def test_create_ticket_redis_unavailable_degrade(mock_redis, mock_session_cls):
    """Redis 不可用时降级：跳过幂等检查，仍能创建工单。"""
    session = _make_session()
    _bind_session(mock_session_cls, session)
    redis = _make_redis(connected=False)
    mock_redis.return_value = redis

    result = await create_ticket("降级标题", "降级描述")

    assert result["created"] is True
    assert result["ticket_id"].startswith("TK-")
    # Redis 不可用，不应尝试加锁
    redis.client.set.assert_not_called()
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


# ---------- 8. list_tickets 过滤 ----------


@patch("app.core.ticket_service.async_session")
@patch("app.core.ticket_service.get_redis")
async def test_list_tickets_with_filter(mock_redis, mock_session_cls):
    """list_tickets 按 status/user_id 过滤，返回 tickets 列表与 total。"""
    session = _make_session()
    ticket_a = Ticket(
        id="TK-a", title="a", description="d", priority="normal", status="open", user_id="u1"
    )
    ticket_b = Ticket(
        id="TK-b", title="b", description="d", priority="normal", status="open", user_id="u1"
    )

    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = [ticket_a, ticket_b]
    count_result = MagicMock()
    count_result.scalar_one.return_value = 2
    # 两次 execute：先列表查询，后计数查询
    session.execute = AsyncMock(side_effect=[list_result, count_result])
    _bind_session(mock_session_cls, session)
    mock_redis.return_value = _make_redis(connected=False)

    result = await list_tickets(status="open", user_id="u1", limit=10, offset=0)

    assert result["total"] == 2
    assert len(result["tickets"]) == 2
    assert result["tickets"][0]["id"] == "TK-a"
    assert result["tickets"][1]["id"] == "TK-b"
    # 两次 execute（列表 + 计数）
    assert session.execute.await_count == 2
