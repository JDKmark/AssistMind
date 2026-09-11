"""工单路由：创建 / 列表 / 状态流转。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core import audit_service
from app.core.ticket_service import (
    add_reply,
    create_ticket,
    get_ticket,
    list_replies,
    list_tickets,
    list_updates,
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


class TicketReplyRequest(BaseModel):
    """工单回复请求（人工介入线程）。"""
    content: str = Field(..., min_length=1, max_length=2000, description="回复内容")


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
    priority: str | None = Query(None, description="优先级过滤"),
    customer: str | None = Query(None, alias="user_id", description="客户过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出工单，支持按状态、优先级、客户过滤与分页。"""
    is_admin_or_agent = user.get("role") in {"admin", "agent"}
    filtered_user_id = customer if is_admin_or_agent and customer else (
        user.get("username") if user.get("role") == "user" else None
    )
    result = await list_tickets(
        status=ticket_status,
        user_id=filtered_user_id,
        priority=priority,
        limit=limit,
        offset=offset,
    )
    return result


@router.get("/updates")
async def ticket_updates_api(
    user: Annotated[dict, Depends(get_current_user)],
    since: str | None = Query(None, description="上次轮询基线时间（ISO 8601，允许 Z 后缀）"),
):
    """工单更新轮询（前端徽标）。user 仅返回本人工单，agent/admin 全量。

    since 缺省返回空列表（仅同步 server_time 基线）；非法 since 返回 422。
    注意：本路由必须注册在 GET /{ticket_id} 之前，否则被路径参数吞掉。
    """
    try:
        return await list_updates(
            since_iso=since,
            requester_role=user.get("role", "user"),
            requester_username=user.get("username", ""),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="since 参数无效"
        ) from e


@router.patch("/{ticket_id}/status")
async def update_ticket_status_api(
    ticket_id: str,
    req: TicketStatusUpdateRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """工单状态流转。agent/admin 可操作；user 可确认关闭自己的 resolved 工单。

    非法流转返回 400，权限/归属不足返回 403。
    """
    try:
        result = await update_status(
            ticket_id=ticket_id,
            new_status=req.status,
            user_role=user.get("role", "user"),
            user_id=user.get("username", ""),
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e


@router.get("/{ticket_id}")
async def get_ticket_api(
    ticket_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """查询单个工单详情。user 仅可查自己的工单（防横向越权）。"""
    ticket = await get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    if user.get("role") == "user" and ticket.get("user_id") != user.get("username"):
        # 与不存在的工单统一 404 形状，防止他人工单枚举
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    return ticket


@router.get("/{ticket_id}/replies")
async def list_replies_api(
    ticket_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """列出工单回复线程。user 仅可查看自己的工单（404 防枚举，同详情接口）。"""
    ticket = await get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    if user.get("role") == "user" and ticket.get("user_id") != user.get("username"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    return {"ticket_id": ticket_id, "replies": await list_replies(ticket_id)}


@router.post("/{ticket_id}/replies")
async def add_reply_api(
    ticket_id: str,
    req: TicketReplyRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """追加工单回复。user 仅可回复自己的工单；客服/管理员回复即人工介入
    （open 状态自动流转为 in_progress）。"""
    ticket = await get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    if user.get("role") == "user" and ticket.get("user_id") != user.get("username"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工单不存在")
    try:
        reply = await add_reply(
            ticket_id=ticket_id,
            sender_role=user.get("role", "user"),
            sender_username=user.get("username", ""),
            content=req.content,
        )
    except ValueError as e:
        # 语义区分：工单不存在 404；回复内容非法（空白串）400
        msg = str(e)
        code = status.HTTP_404_NOT_FOUND if "不存在" in msg else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=msg) from e
    if user.get("role") in {"agent", "admin"}:
        # 人工介入属客服侧写操作，写审计追责链（record 内部旁路，失败不回滚业务）
        await audit_service.record(
            user.get("username", ""),
            "ticket.reply",
            "ticket",
            ticket_id,
            {"sender_role": user.get("role"), "prev_status": ticket.get("status")},
        )
    return reply
