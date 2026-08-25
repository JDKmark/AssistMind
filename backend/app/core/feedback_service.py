"""反馈服务（异步）。

提供 submit_feedback（提交 1-5 分满意度评价）、list_feedback（bad case 查询与
回流评估集用）、mark_exported（回流后标记，避免重复导出）。
RAG Bad Case 闭环：query/answer/sources/intent/conversation_id/trace_id 随反馈入库，
score<=2 的样本由 export_feedback_badcases.py 回流评估集参与回归。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy import func as sa_func

from app.core.infra.postgres import async_session
from app.models.feedback import Feedback

logger = logging.getLogger(__name__)

MIN_SCORE = 1
MAX_SCORE = 5


async def submit_feedback(
    score: int,
    comment: str = "",
    ticket_id: str = "",
    user_id: str = "system",
    conversation_id: str = "",
    trace_id: str = "",
    query: str = "",
    answer: str = "",
    sources: list[dict] | None = None,
    intent: str = "",
    crag_action: str = "",
    degraded: list[str] | None = None,
) -> dict:
    """提交满意度反馈。

    score 必须为 1-5 的整数，越界抛 ValueError。
    sources / degraded 为快照列表，JSON 序列化后入库。
    返回 {"feedback_id": str, "created": True}。
    """
    if not isinstance(score, int) or isinstance(score, bool) or score < MIN_SCORE or score > MAX_SCORE:
        raise ValueError(f"score 必须为 {MIN_SCORE}-{MAX_SCORE} 之间的整数")

    sources_json = _dump_json(sources, "sources")
    degraded_json = _dump_json(degraded, "degraded")

    async with async_session() as session:
        feedback = Feedback(
            score=score,
            comment=comment,
            ticket_id=ticket_id,
            user_id=user_id,
            conversation_id=conversation_id or None,
            trace_id=trace_id or None,
            query=query or None,
            answer=answer or None,
            sources=sources_json,
            intent=intent or None,
            crag_action=crag_action or None,
            degraded=degraded_json,
        )
        session.add(feedback)
        await session.commit()
        await session.refresh(feedback)
        return {"feedback_id": feedback.id, "created": True}


def _dump_json(value: list | None, field: str) -> str | None:
    """序列化快照字段；失败记 warning 并降级为 None（不阻塞反馈提交）。"""
    if not value:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        logger.warning("[Feedback] %s 序列化失败，忽略该快照", field)
        return None


def _load_json(raw: str | None) -> list | None:
    """反序列化快照字段；损坏时返回 None。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


async def list_feedback(
    score: int | None = None,
    exported: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """分页查询反馈（Bad Case 归因/回流用，admin 可见）。

    Args:
        score: 按评分精确过滤（如 2 表示只看差评）
        exported: 按是否已回流过滤（False = 待回流样本）
        page / page_size: 分页
    Returns:
        {"total": int, "items": [{id, score, comment, ticket_id, user_id,
            conversation_id, trace_id, query, answer, sources, intent,
            crag_action, degraded, exported, created_at}]}
    """
    async with async_session() as session:
        stmt = select(Feedback)
        if score is not None:
            stmt = stmt.where(Feedback.score == score)
        if exported is not None:
            stmt = stmt.where(Feedback.exported == exported)
        total = await session.scalar(
            select(sa_func.count()).select_from(stmt.subquery())
        )
        rows = (
            (await session.execute(
                stmt.order_by(Feedback.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ))
            .scalars()
            .all()
        )

    def _dump(f: Feedback) -> dict[str, Any]:
        return {
            "id": f.id,
            "score": f.score,
            "comment": f.comment or "",
            "ticket_id": f.ticket_id or "",
            "user_id": f.user_id,
            "conversation_id": f.conversation_id or "",
            "trace_id": f.trace_id or "",
            "query": f.query or "",
            "answer": f.answer or "",
            "sources": _load_json(f.sources),
            "intent": f.intent or "",
            "crag_action": f.crag_action or "",
            "degraded": _load_json(f.degraded) or [],
            "exported": bool(f.exported),
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }

    return {"total": total or 0, "items": [_dump(f) for f in rows]}


async def mark_exported(feedback_ids: list[str]) -> int:
    """把指定反馈标记为已回流评估集（幂等）。

    Returns:
        实际更新的行数。
    """
    if not feedback_ids:
        return 0
    async with async_session() as session:
        from sqlalchemy import update

        result = await session.execute(
            update(Feedback)
            .where(Feedback.id.in_(feedback_ids))
            .values(exported=True)
        )
        await session.commit()
        return result.rowcount or 0
