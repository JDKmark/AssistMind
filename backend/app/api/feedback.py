"""满意度反馈路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.feedback_service import submit_feedback

router = APIRouter()


class FeedbackRequest(BaseModel):
    """反馈提交请求。"""
    score: int = Field(..., ge=1, le=5, description="评分 1-5")
    comment: str = Field("", max_length=1000, description="文本反馈")
    ticket_id: str = Field("", description="关联工单 ID（可选）")


@router.post("/")
async def submit_feedback_api(
    req: FeedbackRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """提交满意度评价。score 必须 1-5，越界返回 422。"""
    try:
        result = await submit_feedback(
            score=req.score,
            comment=req.comment,
            ticket_id=req.ticket_id,
            user_id=user.get("username", "system"),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
