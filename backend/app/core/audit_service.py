"""管理员操作审计服务。"""

from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.core.infra.postgres import async_session
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


def _to_dict(item: AuditLog) -> dict:
    return {
        "id": item.id,
        "actor_username": item.actor_username,
        "action": item.action,
        "target_type": item.target_type,
        "target_id": item.target_id,
        "detail": item.detail or {},
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


async def record(
    actor_username: str,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict,
) -> dict:
    """记录审计；存储失败时旁路返回，不影响已完成的业务变更。"""
    try:
        async with async_session() as session:
            item = AuditLog(
                actor_username=actor_username,
                action=action,
                target_type=target_type,
                target_id=target_id,
                detail=detail,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            result = _to_dict(item)
            result["recorded"] = True
            return result
    except Exception as exc:
        logger.warning("[audit] 审计写入失败，旁路继续: %s", exc)
        return {"recorded": False}


async def list_logs(
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """按操作者、动作、目标类型分页查询审计记录。"""
    async with async_session() as session:
        stmt = select(AuditLog)
        count_stmt = select(func.count(AuditLog.id))
        filters = []
        if actor is not None:
            filters.append(AuditLog.actor_username == actor)
        if action is not None:
            filters.append(AuditLog.action == action)
        if target_type is not None:
            filters.append(AuditLog.target_type == target_type)
        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)
        stmt = stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        count_result = await session.execute(count_stmt)
        return {
            "items": [_to_dict(item) for item in result.scalars().all()],
            "total": count_result.scalar_one(),
        }
