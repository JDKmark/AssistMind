from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.admin_service import get_overview, update_user
from app.models.user import User


def _context(session):
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def _session_for(user):
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    session.execute = AsyncMock(return_value=result)
    return session


async def test_update_user_role_commits_and_records_audit():
    user = User(id="user-1", username="alice", hashed_password="hash", role="user")
    session = _session_for(user)

    with (
        patch("app.core.admin_service.async_session", return_value=_context(session)),
        patch("app.core.admin_service.record", new=AsyncMock()) as record,
    ):
        result = await update_user("user-1", "admin", role="agent")

    assert result["role"] == "agent"
    session.commit.assert_awaited_once()
    record.assert_awaited_once_with(
        "admin",
        "user.role.update",
        "user",
        "user-1",
        {"old_role": "user", "new_role": "agent"},
    )


async def test_update_user_rejects_admin_role_without_database_access():
    with patch("app.core.admin_service.async_session") as session_factory, pytest.raises(
        ValueError
    ):
        await update_user("user-1", "admin", role="admin")

    session_factory.assert_not_called()


async def test_update_user_rejects_admin_target_without_commit():
    user = User(id="admin-1", username="admin", hashed_password="hash", role="admin")
    session = _session_for(user)

    with patch(
        "app.core.admin_service.async_session", return_value=_context(session)
    ), pytest.raises(PermissionError):
        await update_user("admin-1", "admin", is_active=False)

    session.commit.assert_not_awaited()

async def test_overview_aggregates_ticket_and_feedback_distributions():
    """概览的 tickets / feedback 走服务层聚合方法（全量口径，非首页切片）。

    回归背景：旧实现用 list_tickets()（默认 limit=50）和
    list_feedback(page_size=1) 取一页再在 items 上做 sum，导致 by_status 只覆盖
    前 50 条、negative / pending_export 恒为 0 或 1。现改为调用
    ticket_service.count_by_status / feedback_service.feedback_stats。
    """
    with (
        patch(
            "app.core.admin_service.mall_ds.overview_stats",
            new=AsyncMock(
                return_value={
                    "orders": {"total": 0, "amount": 0.0, "by_status": {}},
                    "refunds": {"total": 0, "amount": 0.0, "by_status": {}},
                }
            ),
        ),
        patch(
            "app.core.admin_service.ticket_service.count_by_status",
            new=AsyncMock(return_value={"open": 2, "resolved": 1}),
        ),
        patch(
            "app.core.admin_service.feedback_service.feedback_stats",
            new=AsyncMock(
                return_value={"total": 3, "negative": 2, "pending_export": 2}
            ),
        ),
        patch("app.core.admin_service.async_session", side_effect=RuntimeError("postgres down")),
        patch("app.core.admin_service.get_qdrant") as qdrant,
    ):
        qdrant.return_value.is_connected = False
        result = await get_overview()

    assert result["tickets"] == {
        "total": 3,
        "by_status": {"open": 2, "resolved": 1},
    }
    assert result["feedback"] == {
        "total": 3,
        "negative": 2,
        "pending_export": 2,
    }


async def test_overview_isolates_section_failures():
    """分区降级隔离：任一数据源故障只影响自己的分区，其余分区照常返回。

    这是概览接口对调用方可见的行为——后台首页不会因为 PostgreSQL 挂了就整页 500，
    也不会把已成功分区的数据吞掉。
    """
    with (
        patch(
            "app.core.admin_service.mall_ds.overview_stats",
            new=AsyncMock(side_effect=RuntimeError("pg down")),
        ),
        patch(
            "app.core.admin_service.ticket_service.count_by_status",
            new=AsyncMock(return_value={"open": 2}),
        ),
        patch(
            "app.core.admin_service.feedback_service.feedback_stats",
            new=AsyncMock(return_value={"total": 5, "negative": 1, "pending_export": 3}),
        ),
        patch("app.core.admin_service.async_session", side_effect=RuntimeError("postgres down")),
        patch("app.core.admin_service.get_qdrant") as qdrant,
    ):
        qdrant.return_value.is_connected = False
        result = await get_overview()

    # 故障分区保留零值默认，不抛异常
    assert result["orders"] == {"total": 0, "amount": 0, "by_status": {}}
    assert result["users"] == {"total": 0, "by_role": {}}
    assert result["knowledge"] == {"documents": 0, "chunks": 0}
    # 正常分区照常返回
    assert result["tickets"] == {"total": 2, "by_status": {"open": 2}}
    assert result["feedback"] == {"total": 5, "negative": 1, "pending_export": 3}
    # degraded 按分区顺序去重标记
    assert result["degraded"] == ["mall", "postgres", "qdrant"]
