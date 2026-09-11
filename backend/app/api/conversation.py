"""会话历史路由：会话列表 / 历史消息回看。

GET /api/v1/conversations                      当前用户会话列表（updated_at 倒序，分页）
GET /api/v1/conversations/{id}/messages        会话历史消息（按 id 正序）

隔离语义：仅返回/回看当前用户（JWT username）的会话；他人会话或不存在统一
404 detail="会话不存在"（防枚举，与工单详情同策略）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.core.conversation_service import list_conversations, list_messages

router = APIRouter()


@router.get("")
async def list_conversations_api(
    user: Annotated[dict, Depends(get_current_user)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """当前用户的会话列表（按 updated_at 倒序，支持 limit/offset）。"""
    return await list_conversations(
        user_id=user.get("username", ""), limit=limit, offset=offset
    )


@router.get("/{conversation_id}/messages")
async def list_messages_api(
    conversation_id: str,
    user: Annotated[dict, Depends(get_current_user)],
):
    """会话历史消息（本人会话；他人/不存在统一 404 防枚举）。"""
    try:
        return await list_messages(conversation_id, user_id=user.get("username", ""))
    except PermissionError:
        raise HTTPException(status_code=404, detail="会话不存在") from None
