"""满意度反馈路由。

POST /api/v1/feedback/        提交满意度评价（1-5 分 + 评论 + 可选会话/trace/回答快照）
GET  /api/v1/feedback/        查询反馈（admin）：按评分/回流状态过滤 + 分页，Bad Case 归因用
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, require_admin
from app.config import get_settings
from app.core.feedback_service import list_feedback, submit_feedback

router = APIRouter()
settings = get_settings()


class FeedbackRequest(BaseModel):
    """反馈提交请求。

    conversation_id / trace_id / query / answer / sources / intent 为
    RAG Bad Case 闭环字段：score<=2 的样本据此归因并回流评估集。
    crag_action / degraded 为本次问答的决策与降级快照（可视化追溯展示）。
    """
    score: int = Field(..., ge=1, le=5, description="评分 1-5")
    comment: str = Field("", max_length=1000, description="文本反馈")
    ticket_id: str = Field("", max_length=32, description="关联工单 ID（可选）")
    conversation_id: str = Field("", max_length=64, description="关联会话 ID（可选）")
    trace_id: str = Field("", max_length=64, description="Langfuse trace ID（可选）")
    query: str = Field("", max_length=2000, description="问题快照")
    answer: str = Field("", max_length=8000, description="回答快照")
    sources: list[dict] | None = Field(None, description="检索来源快照")
    intent: str = Field("", max_length=16, description="意图")
    crag_action: str = Field("", max_length=16, description="CRAG 决策（generate/rewrite_retry/no_result）")
    degraded: list[str] | None = Field(None, description="降级项列表")


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
            conversation_id=req.conversation_id,
            trace_id=req.trace_id,
            query=req.query,
            answer=req.answer,
            sources=req.sources,
            intent=req.intent,
            crag_action=req.crag_action,
            degraded=req.degraded,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.get("/")
async def list_feedback_api(
    user: Annotated[dict, Depends(require_admin)],
    score: int | None = Query(None, ge=1, le=5, description="按评分过滤"),
    exported: bool | None = Query(None, description="按是否已回流评估集过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """查询反馈（admin）：Bad Case 归因 / 回流评估集前的样本筛选。

    响应附带 langfuse_host：前端用它拼接「在 Langfuse 查看 trace」跳转链接
    （trace_id 非空且已配置 Langfuse 时展示）。
    """
    result = await list_feedback(
        score=score,
        exported=exported,
        page=page,
        page_size=page_size,
    )
    result["langfuse_host"] = settings.LANGFUSE_HOST or ""
    return result
