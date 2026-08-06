"""满意度反馈模型。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class Feedback(Base):
    """满意度反馈表。

    score 为 1-5 整数评分；comment / ticket_id 可选。
    """

    __tablename__ = "feedbacks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    score: Mapped[int] = mapped_column(Integer)  # 1-5
    comment: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    ticket_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default="")  # 关联工单，可选
    user_id: Mapped[str] = mapped_column(String(36), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
