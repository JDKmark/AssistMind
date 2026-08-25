from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.admin_service import update_user
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
    ticket_data = {
        "tickets": [
            {"status": "open"},
            {"status": "open"},
            {"status": "resolved"},
        ],
        "total": 3,
    }
    feedback_data = {
        "items": [
            {"score": 2, "exported": False},
            {"score": 1, "exported": True},
            {"score": 5, "exported": False},
        ],
        "total": 3,
    }
    with (
        patch("app.core.admin_service.mall_ds.list_orders", new=AsyncMock(return_value={"orders": [], "total": 0})),
        patch("app.core.admin_service.mall_ds.list_refunds", new=AsyncMock(return_value={"refunds": [], "total": 0})),
        patch("app.core.admin_service.ticket_service.list_tickets", new=AsyncMock(return_value=ticket_data)),
        patch("app.core.admin_service.feedback_service.list_feedback", new=AsyncMock(return_value=feedback_data)),
        patch("app.core.admin_service.async_session", side_effect=RuntimeError("postgres down")),
        patch("app.core.admin_service.get_qdrant") as qdrant,
    ):
        qdrant.return_value.is_connected = False
        result = await __import__("app.core.admin_service", fromlist=["get_overview"]).get_overview()

    assert result["tickets"] == {
        "total": 3,
        "by_status": {"open": 2, "resolved": 1},
    }
    assert result["feedback"] == {
        "total": 3,
        "negative": 2,
        "pending_export": 2,
    }
