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
    {query: str, history?: list[{role, content}], persona?: str}
    """

    query: str = Field(..., min_length=1, description="用户问题")
    history: list[ChatMessage] | None = Field(
        None, description="对话历史（可选）"
    )
    persona: str | None = Field(
        None, description="客服人格 id（可选，见 personas.json；未知值回落默认语气）"
    )
    conversation_id: str = Field(
        "",
        description="会话 id（可选；继续历史对话时传入，该轮消息归属原会话，缺省服务端生成新会话）",
    )
