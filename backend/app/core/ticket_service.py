"""工单服务（异步）。

提供：
- create_ticket：幂等创建（Redis 分布式锁 + 5 分钟内查重）
- list_tickets：按 status / user_id 过滤分页
- update_status：状态机流转 + 角色权限校验
- get_ticket：按 ID 查询

幂等策略：
- Redis 可用且锁获取成功（SET NX EX 300）：查重，有则返回已存在，无则创建
- Redis 可用但锁获取失败（被其他请求持有）：直接查重返回最近 5 分钟内相同工单
- Redis 不可用：降级直接创建（跳过幂等，记录日志）
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infra.postgres import async_session
from app.core.infra.redis import get_redis
from app.models.ticket import Ticket, generate_ticket_id

logger = logging.getLogger(__name__)

# 幂等窗口（秒）：5 分钟内相同 title+description 视为重复
IDEMPOTENCY_WINDOW_SECONDS = 300
# 分布式锁 TTL（秒）
LOCK_TTL = 300

# 状态机：合法流转
VALID_TRANSITIONS: dict[str, set[str]] = {
    "open": {"in_progress"},
    "in_progress": {"resolved"},
    "resolved": {"closed"},
    "closed": set(),
}

# 角色权限：每条流转允许的角色
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "open->in_progress": {"agent", "admin"},
    "in_progress->resolved": {"agent", "admin"},
    "resolved->closed": {"admin"},
}


def _ticket_to_dict(ticket: Ticket) -> dict:
    """工单对象转字典（用于服务返回）。"""
    return {
        "id": ticket.id,
        "title": ticket.title,
        "description": ticket.description,
        "priority": ticket.priority,
        "status": ticket.status,
        "category": ticket.category,
        "user_id": ticket.user_id,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
    }


def _lock_key(title: str, description: str) -> str:
    """根据 title+description 生成幂等锁 key。"""
    digest = hashlib.md5((title + description).encode("utf-8")).hexdigest()
    return f"ticket:lock:{digest}"


async def _find_recent_duplicate(
    session: AsyncSession, title: str, description: str
) -> Ticket | None:
    """查找幂等窗口内 title+description 相同的最近工单。"""
    # DB 列为 TIMESTAMP WITHOUT TIME ZONE（naive），threshold 用 naive UTC 保持一致
    threshold = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=IDEMPOTENCY_WINDOW_SECONDS)
    stmt = (
        select(Ticket)
        .where(Ticket.title == title, Ticket.description == description, Ticket.created_at >= threshold)
        .order_by(Ticket.created_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _persist_ticket(
    session: AsyncSession,
    title: str,
    description: str,
    priority: str,
    user_id: str,
    category: str,
) -> Ticket:
    """构造并持久化一个新工单。"""
    ticket = Ticket(
        id=generate_ticket_id(),
        title=title,
        description=description,
        priority=priority,
        status="open",
        category=category,
        user_id=user_id,
    )
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    return ticket


async def create_ticket(
    title: str,
    description: str,
    priority: str = "normal",
    user_id: str = "system",
    category: str = "",
) -> dict:
    """创建工单（幂等）。

    返回 {"ticket_id": str, "created": bool, "ticket": {...}}。
    """
    redis = get_redis()
    lock_key = _lock_key(title, description)

    acquired = False
    if redis.is_connected:
        try:
            client = redis.client
            if client is not None:
                # SET NX EX：成功返回 True，已被持有返回 None
                acquired = await client.set(lock_key, "1", nx=True, ex=LOCK_TTL)
        except Exception as e:
            logger.warning("[ticket] 获取分布式锁失败，降级处理: %s", e)
            acquired = False

    try:
        if acquired:
            # 锁获取成功：查重，有则返回已存在，无则创建
            async with async_session() as session:
                existing = await _find_recent_duplicate(session, title, description)
                if existing is not None:
                    return {
                        "ticket_id": existing.id,
                        "created": False,
                        "ticket": _ticket_to_dict(existing),
                    }
                ticket = await _persist_ticket(session, title, description, priority, user_id, category)
                return {"ticket_id": ticket.id, "created": True, "ticket": _ticket_to_dict(ticket)}
        elif redis.is_connected:
            # 锁获取失败（被其他请求持有）：直接查重返回
            async with async_session() as session:
                existing = await _find_recent_duplicate(session, title, description)
                if existing is not None:
                    return {
                        "ticket_id": existing.id,
                        "created": False,
                        "ticket": _ticket_to_dict(existing),
                    }
                # 极端情况：锁被持有但查不到重复（对方尚未提交），仍创建避免请求堆积
                ticket = await _persist_ticket(session, title, description, priority, user_id, category)
                return {"ticket_id": ticket.id, "created": True, "ticket": _ticket_to_dict(ticket)}
        else:
            # Redis 不可用：降级直接创建（跳过幂等）
            logger.warning("[ticket] Redis 不可用，跳过幂等检查直接创建")
            async with async_session() as session:
                ticket = await _persist_ticket(session, title, description, priority, user_id, category)
                return {"ticket_id": ticket.id, "created": True, "ticket": _ticket_to_dict(ticket)}
    finally:
        if acquired and redis.is_connected:
            try:
                client = redis.client
                if client is not None:
                    await client.delete(lock_key)
            except Exception as e:
                logger.warning("[ticket] 释放分布式锁失败: %s", e)


async def list_tickets(
    status: str | None = None,
    user_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """列出工单，支持按 status / user_id 过滤。

    返回 {"tickets": [...], "total": int}。
    """
    async with async_session() as session:
        stmt = select(Ticket)
        count_stmt = select(func.count(Ticket.id))
        if status is not None:
            stmt = stmt.where(Ticket.status == status)
            count_stmt = count_stmt.where(Ticket.status == status)
        if user_id is not None:
            stmt = stmt.where(Ticket.user_id == user_id)
            count_stmt = count_stmt.where(Ticket.user_id == user_id)
        stmt = stmt.order_by(Ticket.created_at.desc()).limit(limit).offset(offset)

        result = await session.execute(stmt)
        tickets = result.scalars().all()
        count_result = await session.execute(count_stmt)
        total = count_result.scalar_one()
        return {"tickets": [_ticket_to_dict(t) for t in tickets], "total": total}


async def search_tickets(keyword: str, limit: int = 5) -> list[dict]:
    """按关键词检索工单（title/description 模糊匹配），按创建时间倒序。

    供诊断链路检索相似历史工单（"过去是否出现过相似故障"）。
    keyword 为空返回空列表；PostgreSQL 不可用时异常上抛，由调用方降级。
    """
    if not keyword:
        return []
    async with async_session() as session:
        pattern = f"%{keyword}%"
        stmt = (
            select(Ticket)
            .where(or_(Ticket.title.ilike(pattern), Ticket.description.ilike(pattern)))
            .order_by(Ticket.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return [_ticket_to_dict(t) for t in result.scalars().all()]


async def update_status(
    ticket_id: str,
    new_status: str,
    user_role: str = "user",
) -> dict:
    """流转工单状态（状态机 + 角色权限校验）。

    合法流转：
    - open → in_progress（agent/admin）
    - in_progress → resolved（agent/admin）
    - resolved → closed（admin）

    非法流转抛 ValueError；权限不足抛 PermissionError。
    返回 {"ticket_id": str, "status": str}。
    """
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.id == ticket_id))
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError(f"工单不存在: {ticket_id}")

        current = ticket.status
        # 1. 状态机校验
        if new_status not in VALID_TRANSITIONS.get(current, set()):
            raise ValueError(f"非法状态流转: {current} -> {new_status}")
        # 2. 角色权限校验
        transition_key = f"{current}->{new_status}"
        allowed_roles = ROLE_PERMISSIONS.get(transition_key, set())
        if user_role not in allowed_roles:
            raise PermissionError(f"角色 {user_role} 无权执行流转 {transition_key}")

        ticket.status = new_status
        await session.commit()
        return {"ticket_id": ticket_id, "status": new_status}


async def get_ticket(ticket_id: str) -> dict | None:
    """按 ID 查询工单，不存在返回 None。"""
    async with async_session() as session:
        result = await session.execute(select(Ticket).where(Ticket.id == ticket_id))
        ticket = result.scalar_one_or_none()
        if ticket is None:
            return None
        return _ticket_to_dict(ticket)
