"""会话模型单元测试（不连 DB）。

覆盖：
1. Conversation 表名与关键字段（user_id 索引非空、title 默认、时间戳 server_default/onupdate）
2. ChatMessage 表名与关键字段（自增主键、conversation_id 索引非空、role/content/intent）
3. 两模型注册到 Base.metadata（init_db create_all 依赖此机制建表）
"""

from __future__ import annotations

from sqlalchemy import String, Text

from app.models.conversation import ChatMessage, Conversation
from app.models.user import Base


def test_conversation_tablename_and_fields():
    """Conversation：__tablename__=conversations，关键字段存在。"""
    assert Conversation.__tablename__ == "conversations"
    cols = Conversation.__table__.columns
    # id：String(36) 主键
    assert cols["id"].primary_key
    assert isinstance(cols["id"].type, String)
    assert cols["id"].type.length == 36
    # user_id：String(64)、非空、有索引
    assert cols["user_id"].nullable is False
    assert cols["user_id"].index is True
    assert cols["user_id"].type.length == 64
    # title：默认空串
    assert cols["title"].default is not None
    assert cols["title"].default.arg == ""
    # 时间戳列存在
    assert "created_at" in cols
    assert "updated_at" in cols
    assert cols["updated_at"].server_default is not None
    assert cols["updated_at"].onupdate is not None


def test_chat_message_tablename_and_fields():
    """ChatMessage：__tablename__=chat_messages，关键字段存在。"""
    assert ChatMessage.__tablename__ == "chat_messages"
    cols = ChatMessage.__table__.columns
    # id：自增整型主键
    assert cols["id"].primary_key
    # conversation_id：String(36)、非空、有索引
    assert cols["conversation_id"].nullable is False
    assert cols["conversation_id"].index is True
    assert cols["conversation_id"].type.length == 36
    # role：String(16) 非空
    assert cols["role"].nullable is False
    assert cols["role"].type.length == 16
    # content：Text 非空
    assert isinstance(cols["content"].type, Text)
    assert cols["content"].nullable is False
    # intent：默认空串
    assert cols["intent"].default is not None
    assert cols["intent"].default.arg == ""
    # created_at 存在
    assert "created_at" in cols
    assert cols["created_at"].server_default is not None


def test_models_registered_in_metadata():
    """两模型注册到 Base.metadata（init_db 的 create_all 才会建表）。"""
    assert "conversations" in Base.metadata.tables
    assert "chat_messages" in Base.metadata.tables
