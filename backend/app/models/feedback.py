"""满意度反馈模型。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class Feedback(Base):
    """满意度反馈表。

    score 为 1-5 整数评分；comment / ticket_id 可选。

    RAG Bad Case 闭环字段：
    - conversation_id：关联会话（chat/faq 链路生成的会话 ID，来源见 api/chat）
    - trace_id：Langfuse trace ID（按会话归因检索/生成证据链）
    - query / answer：问题与回答快照（回流评估集时的低分样本来源）
    - sources：检索来源快照（JSON 文本，[{title, source, doc_id, snippet, text, score}]）
    - intent：意图（faq/task/chat/unclear/diagnose）
    - crag_action：本次问答的 CRAG 决策（generate/rewrite_retry/no_result，归因用）
    - degraded：降级项列表（JSON 文本，如 ["reranker"]，归因用）
    - exported：是否已回流评估集（供 export_feedback_badcases.py 增量导出）
    """

    __tablename__ = "feedbacks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    score: Mapped[int] = mapped_column(Integer)  # 1-5
    comment: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    ticket_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default="")  # 关联工单，可选
    user_id: Mapped[str] = mapped_column(String(36), default="system")
    conversation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")  # Langfuse trace ID
    query: Mapped[str | None] = mapped_column(Text, nullable=True, default="")  # 问题快照
    answer: Mapped[str | None] = mapped_column(Text, nullable=True, default="")  # 回答快照
    sources: Mapped[str | None] = mapped_column(Text, nullable=True, default="")  # 检索来源快照（JSON 文本）
    intent: Mapped[str | None] = mapped_column(String(16), nullable=True, default="")  # 意图
    crag_action: Mapped[str | None] = mapped_column(String(16), nullable=True, default="")  # generate/rewrite_retry/no_result
    degraded: Mapped[str | None] = mapped_column(Text, nullable=True, default="")  # 降级项列表（JSON 文本）
    exported: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否已回流评估集
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
