from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.core.audit_service import list_logs, record
from app.models.audit import AuditLog


def _session_context(session):
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


async def test_record_persists_audit_log():
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    audit = AuditLog(
        id="audit-1",
        actor_username="admin",
        action="user.role.update",
        target_type="user",
        target_id="user-1",
        detail={"old_role": "user", "new_role": "agent"},
    )
    session.refresh.side_effect = lambda item: setattr(item, "id", audit.id)

    with patch("app.core.audit_service.async_session", return_value=_session_context(session)):
        result = await record(
            actor_username="admin",
            action="user.role.update",
            target_type="user",
            target_id="user-1",
            detail={"old_role": "user", "new_role": "agent"},
        )

    assert result["actor_username"] == "admin"
    assert result["action"] == "user.role.update"
    assert result["detail"] == {"old_role": "user", "new_role": "agent"}
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


async def test_list_logs_filters_and_paginates():
    session = AsyncMock()
    rows = [
        AuditLog(
            id="audit-1",
            actor_username="admin",
            action="user.status.update",
            target_type="user",
            target_id="user-1",
            detail={"old_is_active": True, "new_is_active": False},
        )
    ]
    items_result = MagicMock()
    items_result.scalars.return_value.all.return_value = rows
    count_result = MagicMock()
    count_result.scalar_one.return_value = 1
    session.execute = AsyncMock(side_effect=[items_result, count_result])

    with patch("app.core.audit_service.async_session", return_value=_session_context(session)):
        result = await list_logs(action="user.status.update", limit=10, offset=0)

    assert result["total"] == 1
    assert result["items"][0]["target_id"] == "user-1"
    assert result["items"][0]["detail"]["new_is_active"] is False
    assert session.execute.await_count == 2


async def test_record_failure_is_bypassed_and_warned(caplog):
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock(side_effect=RuntimeError("database unavailable"))

    with patch("app.core.audit_service.async_session", return_value=_session_context(session)):
        result = await record(
            actor_username="admin",
            action="user.role.update",
            target_type="user",
            target_id="user-1",
            detail={"new_role": "agent"},
        )

    assert result["recorded"] is False
    assert "database unavailable" in caplog.text
