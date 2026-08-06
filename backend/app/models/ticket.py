"""工单模型。

工单 ID 格式：TK-{时间戳yyyymmddHHMMSS}{7位随机数字}。
后缀位数由 settings.TICKET_ID_RANDOM_SUFFIX 控制（>=7），否则高并发下 UNIQUE 约束碰撞。
"""

from __future__ import annotations

import random
import time
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.models.user import Base

settings = get_settings()

# 合法取值集合（用于服务层校验，也作为文档说明）
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}
VALID_STATUSES = {"open", "in_progress", "resolved", "closed"}


def generate_ticket_id() -> str:
    """生成工单 ID：TK-{时间戳}{随机数字后缀}。

    时间戳格式 yyyymmddHHMMSS（14 位），后缀位数由 settings.TICKET_ID_RANDOM_SUFFIX
    决定（默认 7，必须 >=7 以避免高并发 UNIQUE 碰撞）。
    """
    timestamp = time.strftime("%Y%m%d%H%M%S")
    suffix = "".join(random.choices("0123456789", k=settings.TICKET_ID_RANDOM_SUFFIX))
    return f"TK-{timestamp}{suffix}"


class Ticket(Base):
    """工单表。"""

    __tablename__ = "tickets"

    # TK- + 14 位时间戳 + 7 位随机 = 24 位，String(32) 足够
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=generate_ticket_id)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # low|normal|high|urgent
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|in_progress|resolved|closed
    category: Mapped[str] = mapped_column(String(32), default="")  # 标记"转人工"等
    # 用 String 软外键指向 users.id，不强约束以便测试
    user_id: Mapped[str] = mapped_column(String(36), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
