"""管理员用户管理服务。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import func, or_, select

from app.core import feedback_service, ticket_service
from app.core.audit_service import record
from app.core.infra.postgres import async_session
from app.core.infra.qdrant import get_qdrant
from app.core.mall import data_source as mall_ds
from app.models.user import User

logger = logging.getLogger(__name__)


def _to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


async def list_users(
    role: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    async with async_session() as session:
        stmt = select(User)
        count_stmt = select(func.count(User.id))
        filters = []
        if role is not None:
            filters.append(User.role == role)
        if keyword:
            pattern = f"%{keyword}%"
            filters.append(or_(User.username.ilike(pattern), User.id.ilike(pattern)))
        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)
        stmt = stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        count_result = await session.execute(count_stmt)
        return {
            "users": [_to_dict(user) for user in result.scalars().all()],
            "total": count_result.scalar_one(),
        }


async def update_user(
    user_id: str,
    operator: str,
    role: str | None = None,
    is_active: bool | None = None,
) -> dict:
    if role is None and is_active is None:
        raise ValueError("至少提供 role 或 is_active")
    if role is not None and role not in {"user", "agent"}:
        raise ValueError("角色只能为 user 或 agent")

    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise LookupError("用户不存在")
        if user.role == "admin":
            raise PermissionError("不能修改管理员账号")

        old_role = user.role
        old_is_active = user.is_active
        actions = []
        if role is not None and role != old_role:
            user.role = role
            actions.append(("user.role.update", {"old_role": old_role, "new_role": role}))
        if is_active is not None and is_active != old_is_active:
            user.is_active = is_active
            actions.append(
                (
                    "user.status.update",
                    {"old_is_active": old_is_active, "new_is_active": is_active},
                )
            )
        await session.commit()
        await session.refresh(user)
        updated = _to_dict(user)

    for action, detail in actions:
        await record(operator, action, "user", user.id, detail)
    return updated


async def update_refund_status(refund_id: str, new_status: str, operator: str) -> dict:
    result = await mall_ds.update_refund_status(refund_id, new_status)
    await record(
        operator,
        "refund.status.update",
        "refund",
        refund_id,
        {"new_status": new_status, "order_sn": result.get("order_sn")},
    )
    return result


async def _load_section(
    label: str,
    loader: Callable[[list[str]], Awaitable[dict]],
    degraded: list[str],
) -> dict:
    """加载单个概览分区，返回该分区要合并进 overview 的键。

    失败只影响自己：记 warning + 标记 degraded，返回空 dict 让调用方保留零值默认。
    """
    try:
        return await loader(degraded)
    except Exception as exc:
        logger.warning("[admin] %s 概览失败: %s", label, exc)
        degraded.append(label)
        return {}


async def _mall_overview(degraded: list[str]) -> dict:
    stats = await mall_ds.overview_stats()
    degraded.extend(stats.get("degraded", []))
    return {"orders": stats["orders"], "refunds": stats["refunds"]}


async def _user_overview(_degraded: list[str]) -> dict:
    async with async_session() as session:
        total = await session.scalar(select(func.count(User.id)))
        role_rows = await session.execute(
            select(User.role, func.count(User.id)).group_by(User.role)
        )
        return {"users": {"total": total or 0, "by_role": dict(role_rows.all())}}


async def _ticket_overview(_degraded: list[str]) -> dict:
    by_status = await ticket_service.count_by_status()
    return {"tickets": {"total": sum(by_status.values()), "by_status": by_status}}


async def _feedback_overview(_degraded: list[str]) -> dict:
    return {"feedback": await feedback_service.feedback_stats()}


async def _knowledge_overview(_degraded: list[str]) -> dict:
    qdrant = get_qdrant()
    if not qdrant.is_connected:
        raise RuntimeError("qdrant unavailable")
    chunks = await qdrant.scroll_all()
    documents = {chunk.get("doc_id") for chunk in chunks if chunk.get("doc_id")}
    return {"knowledge": {"documents": len(documents), "chunks": len(chunks)}}


# 分区加载顺序即 degraded 标记顺序（与接口文档一致，改动需同步测试）
_OVERVIEW_SECTIONS: tuple[tuple[str, Callable[[list[str]], Awaitable[dict]]], ...] = (
    ("mall", _mall_overview),
    ("postgres", _user_overview),
    ("tickets", _ticket_overview),
    ("feedback", _feedback_overview),
    ("qdrant", _knowledge_overview),
)


async def get_overview() -> dict:
    """管理端概览：各分区独立降级，任一数据源故障不拖垮其余分区。"""
    degraded: list[str] = []
    overview = {
        "orders": {"total": 0, "amount": 0, "by_status": {}},
        "tickets": {"total": 0, "by_status": {}},
        "refunds": {"total": 0, "by_status": {}},
        "users": {"total": 0, "by_role": {}},
        "feedback": {"total": 0, "negative": 0, "pending_export": 0},
        "knowledge": {"documents": 0, "chunks": 0},
    }
    for label, loader in _OVERVIEW_SECTIONS:
        overview.update(await _load_section(label, loader, degraded))
    overview["degraded"] = list(dict.fromkeys(degraded))
    return overview
