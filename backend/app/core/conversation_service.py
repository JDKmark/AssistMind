"""会话服务（异步）。

提供：
- save_round：一轮问答持久化（upsert 会话 + user/assistant 两条消息）
- list_conversations：当前用户会话列表（updated_at 倒序，含 message_count）
- list_messages：会话消息回看（非本人/不存在统一抛 PermissionError，防枚举）

消费方（chat SSE done 路径）需自行 try/except：持久化失败不得阻塞响应。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infra.postgres import async_session
from app.models.conversation import ChatMessage, Conversation

logger = logging.getLogger(__name__)

# 会话标题取首轮提问前 40 字
_TITLE_MAX_LEN = 40


def _naive_utc_now() -> datetime:
    """DB 列为 TIMESTAMP WITHOUT TIME ZONE（naive），用 naive UTC 保持一致。"""
    return datetime.now(UTC).replace(tzinfo=None)


async def _get_conversation(session: AsyncSession, conversation_id: str) -> Conversation | None:
    result = await session.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    return result.scalar_one_or_none()


async def save_round(
    conversation_id: str,
    user_id: str,
    query: str,
    answer: str,
    intent: str = "",
) -> None:
    """持久化一轮问答：upsert 会话 + 插入 user/assistant 两条消息。

    会话不存在则新建（title=问题前 40 字）；已存在则复用并触碰 updated_at
    （让"继续对话"的会话在列表中浮到最上）。非本人会话抛 PermissionError
    （conversation_id 由客户端传入，写路径与读路径同策略防跨用户注入）。
    异常上抛，由调用方降级。

    并发：同一 conversation_id 两路 SSE 同时完成首轮时，双 INSERT 会主键冲突
    （IntegrityError）——捕获后重试一次（重查会话归属，另一事务已建好则正常
    追加消息）；连续两次冲突属极端情况，上抛由调用方降级（_persist_round_safe）。
    """
    for _ in range(2):
        try:
            async with async_session() as session:
                conv = await _get_conversation(session, conversation_id)
                if conv is None:
                    conv = Conversation(
                        id=conversation_id,
                        user_id=user_id,
                        title=(query or "")[:_TITLE_MAX_LEN],
                    )
                    session.add(conv)
                else:
                    if conv.user_id != user_id:
                        raise PermissionError("会话不存在")
                    # 显式触碰 updated_at（无列变更时 ORM 不会发 UPDATE，onupdate 不触发）
                    conv.updated_at = _naive_utc_now()

                session.add(
                    ChatMessage(conversation_id=conversation_id, role="user", content=query)
                )
                session.add(
                    ChatMessage(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=answer,
                        intent=intent,
                    )
                )
                await session.commit()
            return
        except IntegrityError:
            # 并发新建同一会话（另一请求恰好 commit）：告警后重试，重查归属防越权
            logger.warning(
                "[Conversation] 会话主键冲突（并发新建），重查归属后重试: %s",
                conversation_id,
            )
    raise RuntimeError("[Conversation] 会话写入连续冲突（并发），由调用方降级")


async def list_conversations(user_id: str, limit: int = 20, offset: int = 0) -> dict:
    """列出该用户会话（updated_at 倒序，分页），含 message_count（相关子查询计数）。

    返回 {"conversations": [...], "total": N}。
    """
    async with async_session() as session:
        message_count = (
            select(func.count(ChatMessage.id))
            .where(ChatMessage.conversation_id == Conversation.id)
            .correlate(Conversation)
            .scalar_subquery()
        )
        stmt = (
            select(Conversation)
            .add_columns(message_count)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        count_stmt = (
            select(func.count(Conversation.id)).where(Conversation.user_id == user_id)
        )

        result = await session.execute(stmt)
        rows = result.all()
        conversations = [
            {
                "id": conv.id,
                "title": conv.title,
                "user_id": conv.user_id,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
                "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
                "message_count": count or 0,
            }
            for conv, count in rows
        ]
        total = (await session.execute(count_stmt)).scalar_one()
        return {"conversations": conversations, "total": total}


async def list_messages(conversation_id: str, user_id: str) -> dict:
    """返回会话消息（按 id 正序）。

    会话不存在或 user_id 不匹配 → raise PermissionError("会话不存在")
    （API 层统一转 404，防枚举）。
    """
    async with async_session() as session:
        conv = await _get_conversation(session, conversation_id)
        if conv is None or conv.user_id != user_id:
            raise PermissionError("会话不存在")

        result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.id.asc())
        )
        messages = [
            {
                "role": m.role,
                "content": m.content,
                "intent": m.intent,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in result.scalars().all()
        ]
        return {"conversation_id": conversation_id, "messages": messages}
