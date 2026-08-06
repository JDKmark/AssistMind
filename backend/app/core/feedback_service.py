"""反馈服务（异步）。

提供 submit_feedback：提交 1-5 分满意度评价。
"""

from __future__ import annotations

import logging

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
) -> dict:
    """提交满意度反馈。

    score 必须为 1-5 的整数，越界抛 ValueError。
    返回 {"feedback_id": str, "created": True}。
    """
    if not isinstance(score, int) or isinstance(score, bool) or score < MIN_SCORE or score > MAX_SCORE:
        raise ValueError(f"score 必须为 {MIN_SCORE}-{MAX_SCORE} 之间的整数")

    async with async_session() as session:
        feedback = Feedback(
            score=score,
            comment=comment,
            ticket_id=ticket_id,
            user_id=user_id,
        )
        session.add(feedback)
        await session.commit()
        await session.refresh(feedback)
        return {"feedback_id": feedback.id, "created": True}
