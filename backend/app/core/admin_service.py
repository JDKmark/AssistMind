"""管理员用户管理服务。"""

from __future__ import annotations

import logging

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


async def get_overview() -> dict:
    degraded: list[str] = []
    overview = {
        "orders": {"total": 0, "amount": 0, "by_status": {}},
        "tickets": {"total": 0, "by_status": {}},
        "refunds": {"total": 0, "by_status": {}},
        "users": {"total": 0, "by_role": {}},
        "feedback": {"total": 0, "negative": 0, "pending_export": 0},
        "knowledge": {"documents": 0, "chunks": 0},
        "degraded": degraded,
    }
    try:
        orders = await mall_ds.list_orders()
        overview["orders"] = {
            "total": orders["total"],
            "amount": sum(item.get("pay_amount") or 0 for item in orders["orders"]),
            "by_status": {
                key: sum(1 for item in orders["orders"] if item["status"] == key)
                for key in {item["status"] for item in orders["orders"]}
            },
        }
        refunds = await mall_ds.list_refunds()
        overview["refunds"] = {
            "total": refunds["total"],
            "by_status": {
                key: sum(1 for item in refunds["refunds"] if item["status"] == key)
                for key in {item["status"] for item in refunds["refunds"]}
            },
        }
        degraded.extend(orders.get("degraded", []) + refunds.get("degraded", []))
    except Exception as exc:
        logger.warning("[admin] 商城概览失败: %s", exc)
        degraded.append("mall")
    try:
        async with async_session() as session:
            total = await session.scalar(select(func.count(User.id)))
            role_rows = await session.execute(
                select(User.role, func.count(User.id)).group_by(User.role)
            )
            overview["users"] = {"total": total or 0, "by_role": dict(role_rows.all())}
    except Exception as exc:
        logger.warning("[admin] 用户概览失败: %s", exc)
        degraded.append("postgres")
    for name, loader, key in (
        ("tickets", lambda: ticket_service.list_tickets(), "tickets"),
        ("feedback", lambda: feedback_service.list_feedback(page=1, page_size=1), "feedback"),
    ):
        try:
            data = await loader()
            if key == "tickets":
                tickets = data.get("tickets", [])
                overview[key] = {
                    "total": data["total"],
                    "by_status": {
                        status: sum(1 for item in tickets if item.get("status") == status)
                        for status in {item.get("status") for item in tickets if item.get("status")}
                    },
                }
            else:
                feedback_items = data.get("items", [])
                overview[key] = {
                    "total": data["total"],
                    "negative": sum(1 for item in feedback_items if (item.get("score") or 0) <= 2),
                    "pending_export": sum(
                        1 for item in feedback_items if not item.get("exported", False)
                    ),
                }
        except Exception as exc:
            logger.warning("[admin] %s 概览失败: %s", name, exc)
            degraded.append(name)
    try:
        qdrant = get_qdrant()
        if not qdrant.is_connected:
            raise RuntimeError("qdrant unavailable")
        chunks = await qdrant.scroll_all()
        documents = {chunk.get("doc_id") for chunk in chunks if chunk.get("doc_id")}
        overview["knowledge"] = {"documents": len(documents), "chunks": len(chunks)}
    except Exception as exc:
        logger.warning("[admin] 知识库概览失败: %s", exc)
        degraded.append("qdrant")
    overview["degraded"] = list(dict.fromkeys(degraded))
    return overview
