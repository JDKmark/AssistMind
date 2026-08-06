"""工单路由：创建 / 列表 / 状态流转。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.ticket_service import (
    create_ticket,
    get_ticket,
    list_tickets,
    update_status,
)

router = APIRouter()


class TicketCreateRequest(BaseModel):
    """创建工单请求。"""
    title: str = Field(..., min_length=1, max_length=200, description="工单标题")
    description: str = Field(..., min_length=1, description="问题描述")
    priority: str = Field("normal", description="优先级 low/normal/high/urgent")


class TicketStatusUpdateRequest(BaseModel):
    """状态流转请求。"""
    status: str = Field(..., description="新状态 open/in_progress/resolved/closed")


@router.post("/")
async def create_ticket_api(
    req: TicketCreateRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """创建工单。幂等：5分钟内相同 title+description 返回已存在。"""
    result = await create_ticket(
        title=req.title,
        description=req.description,
        priority=req.priority,
        user_id=user.get("username", "system"),
    )
    return result


@router.get("/list")
async def list_tickets_api(
    user: Annotated[dict, Depends(get_current_user)],
    ticket_status: str | None = Query(None, alias="status", description="状态过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出工单，支持状态过滤与分页。"""
    result = await list_tickets(
        status=ticket_status,
        user_id=user.get("username") if user.get("role") == "user" else None,
        limit=limit,
        offset=offset,
    )
    return result


@router.patch("/{ticket_id}/status")
async def update_ticket_status_api(
    ticket_id: str,
    req: TicketStatusUpdateRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """工单状态流转。agent/admin 可操作，非法流转返回 400。"""
    try:
        result = await update_status(
            ticket_id=ticket_id,
            new_status=req.status,
            user_role=user.get("role", "user"),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.get("/{ticket_id}")
async def get_ticket_api(
    ticket_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """查询单个工单详情。"""
    ticket = await get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    return ticket
