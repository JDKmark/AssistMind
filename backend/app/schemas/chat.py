"""聊天相关 Pydantic 模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """对话历史中的一条消息。"""

    role: str = Field(..., description="消息角色：user / assistant")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    """SSE 流式聊天请求体。

    POST /api/v1/chat/ask
    {query: str, history?: list[{role, content}]}
    """

    query: str = Field(..., min_length=1, description="用户问题")
    history: list[ChatMessage] | None = Field(
        None, description="对话历史（可选）"
    )
