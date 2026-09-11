"""会话服务单元测试。

覆盖：
1. save_round 首轮：新建会话（title=问题前 40 字）+ 两条消息（user/assistant）
2. save_round 续聊：会话已存在则复用（不新建），追加两条消息
3. save_round 越权写入：会话归属他人 → 抛 PermissionError 且不写任何消息
   （conversation_id 由客户端传入，写路径与读路径同策略防跨用户注入）
4. list_messages 非本人会话：抛 PermissionError（API 层转 404）
5. list_messages 会话不存在：同样抛 PermissionError（防枚举）
6. list_messages 本人会话：按 id 正序返回 {role, content, intent, created_at(ISO)}
7. list_conversations：按 updated_at 倒序 + message_count（子查询计数）+ total

mock 策略：mock async_session（同 test_ticket_service 模式），不连真实 DB。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.conversation_service import (
    list_conversations,
    list_messages,
    save_round,
)
from app.models.conversation import Conversation
from sqlalchemy.exc import IntegrityError


def _make_session() -> AsyncMock:
    """构造一个 mock AsyncSession。"""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _bind_session(mock_session_cls, session: AsyncMock) -> None:
    """将 mock session 绑定到 async_session() 上下文管理器。"""
    mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=session)
    mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)


def _make_conv(user_id="u1", cid="c-1") -> Conversation:
    return Conversation(id=cid, user_id=user_id, title="旧会话")


# ---------- 1. save_round 首轮 ----------


@patch("app.core.conversation_service.async_session")
async def test_save_round_first_round_creates_conversation_and_two_messages(mock_session_cls):
    """首轮：会话不存在 → 新建（title=问题前 40 字），插入 user+assistant 两条消息。"""
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    _bind_session(mock_session_cls, session)

    long_query = "这是一个非常长的提问" * 10  # > 40 字
    await save_round("c-new", "u1", long_query, "这是回答", intent="faq")

    # 会话 + 两条消息 = 3 次 add
    assert session.add.call_count == 3
    added = [c.args[0] for c in session.add.call_args_list]
    conv = next(a for a in added if a.__tablename__ == "conversations")
    messages = [a for a in added if a.__tablename__ == "chat_messages"]
    # title 取问题前 40 字
    assert conv.title == long_query[:40]
    assert conv.user_id == "u1"
    assert len(messages) == 2
    roles = {m.role: m for m in messages}
    assert set(roles) == {"user", "assistant"}
    assert roles["user"].content == long_query
    assert roles["assistant"].content == "这是回答"
    assert roles["assistant"].intent == "faq"
    session.commit.assert_awaited_once()


# ---------- 2. save_round 续聊（不新建会话） ----------


@patch("app.core.conversation_service.async_session")
async def test_save_round_existing_conversation_reused(mock_session_cls):
    """续聊：会话已存在 → 复用（不新建，仅追加两条消息）。"""
    session = _make_session()
    existing = _make_conv()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing))
    )
    _bind_session(mock_session_cls, session)

    await save_round("c-1", "u1", "追问", "回答二")

    added = [c.args[0] for c in session.add.call_args_list]
    # 不新建会话：只有两条消息
    assert all(a.__tablename__ == "chat_messages" for a in added)
    assert len(added) == 2
    assert {m.role for m in added} == {"user", "assistant"}
    assert all(m.conversation_id == "c-1" for m in added)
    session.commit.assert_awaited_once()


# ---------- 3. save_round 越权写入 ----------


@patch("app.core.conversation_service.async_session")
async def test_save_round_other_owner_raises_and_no_write(mock_session_cls):
    """会话归属他人：抛 PermissionError，不追加任何消息（与读路径同策略）。"""
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=_make_conv(user_id="u2")))
    )
    _bind_session(mock_session_cls, session)

    with pytest.raises(PermissionError, match="会话不存在"):
        await save_round("c-1", "mallory", "你好", "答")

    session.add.assert_not_called()
    session.commit.assert_not_awaited()


# ---------- 3.5 save_round 并发新建（主键冲突重试） ----------


@patch("app.core.conversation_service.async_session")
async def test_save_round_concurrent_creation_retries_on_integrity_error(mock_session_cls):
    """同一会话两路并发首轮：首次 INSERT 主键冲突 → 重查归属（另一事务已建好）→ 追加成功。

    第一次提交抛 IntegrityError（并发方已抢建）；重试时执行返回已存在的本人会话，
    直接追加两条消息并提交成功。
    """
    sessions = []
    commit_plan = [IntegrityError("dup", {}, Exception("duplicate key")), None]

    def _new_session():
        s = _make_session()
        sessions.append(s)
        return s

    first = _new_session()
    first.execute = AsyncMock(  # 第一次：会话不存在（即将新建）
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    first.commit.side_effect = commit_plan  # 首次 commit 抛主键冲突

    second = _new_session()
    second.execute = AsyncMock(  # 重试：会话已被并发方创建（本人归属）
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=_make_conv(user_id="u1", cid="c-race"))
        )
    )

    mock_session_cls.side_effect = lambda: AsyncMock(
        **{
            "__aenter__": AsyncMock(side_effect=lambda: sessions.pop(0)),
            "__aexit__": AsyncMock(return_value=None),
        }
    )

    await save_round("c-race", "u1", "并发问题", "并发回答")

    # 第二次只追加两条消息（会话复用）
    added = [c.args[0] for c in second.add.call_args_list]
    assert len(added) == 2
    assert all(a.__tablename__ == "chat_messages" for a in added)
    second.commit.assert_awaited_once()


@patch("app.core.conversation_service.async_session")
async def test_save_round_concurrent_conflict_owner_mismatch_still_rejected(mock_session_cls):
    """并发重试路径下归属校验仍生效：重查发现会话属于他人 → PermissionError。"""
    sessions = []

    def _new_session():
        s = _make_session()
        sessions.append(s)
        return s

    first = _new_session()
    first.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    first.commit.side_effect = IntegrityError("dup", {}, Exception("duplicate key"))

    second = _new_session()
    second.execute = AsyncMock(
        return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=_make_conv(user_id="u2", cid="c-race"))
        )
    )

    mock_session_cls.side_effect = lambda: AsyncMock(
        **{
            "__aenter__": AsyncMock(side_effect=lambda: sessions.pop(0)),
            "__aexit__": AsyncMock(return_value=None),
        }
    )

    with pytest.raises(PermissionError, match="会话不存在"):
        await save_round("c-race", "u1", "并发问题", "答")
    second.add.assert_not_called()


# ---------- 4/5. list_messages 越权 / 不存在 ----------


@patch("app.core.conversation_service.async_session")
async def test_list_messages_not_owner_raises(mock_session_cls):
    """非本人会话：抛 PermissionError（API 层转 404）。"""
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=_make_conv(user_id="u2")))
    )
    _bind_session(mock_session_cls, session)

    with pytest.raises(PermissionError):
        await list_messages("c-1", "u1")


@patch("app.core.conversation_service.async_session")
async def test_list_messages_missing_raises_same_permission_error(mock_session_cls):
    """会话不存在：同样抛 PermissionError（与越权同形状，防枚举）。"""
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    _bind_session(mock_session_cls, session)

    with pytest.raises(PermissionError, match="会话不存在"):
        await list_messages("c-404", "u1")


# ---------- 5. list_messages 本人会话 ----------


@patch("app.core.conversation_service.async_session")
async def test_list_messages_own_conversation_ordered_by_id(mock_session_cls):
    """本人会话：消息按 id 正序，字段为 {role, content, intent, created_at(ISO)}。"""
    session = _make_session()

    m1, m2 = MagicMock(), MagicMock()
    m1.id, m2.id = 1, 2
    m1.role, m2.role = "user", "assistant"
    m1.content, m2.content = "问题", "回答"
    m1.intent, m2.intent = "", "faq"
    created = datetime(2026, 8, 30, 12, 0, 0)
    m1.created_at = m2.created_at = created

    conv_result = MagicMock(scalar_one_or_none=MagicMock(return_value=_make_conv(user_id="u1")))
    msg_result = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[m1, m2])))
    )
    session.execute = AsyncMock(side_effect=[conv_result, msg_result])
    _bind_session(mock_session_cls, session)

    result = await list_messages("c-1", "u1")

    assert result["conversation_id"] == "c-1"
    assert [m["role"] for m in result["messages"]] == ["user", "assistant"]
    assert [m["content"] for m in result["messages"]] == ["问题", "回答"]
    assert result["messages"][1]["intent"] == "faq"
    assert result["messages"][0]["created_at"] == created.isoformat()


# ---------- 6. list_conversations ----------


@patch("app.core.conversation_service.async_session")
async def test_list_conversations_shape_with_message_count(mock_session_cls):
    """列表：返回 {conversations: [...含 message_count], total}。"""
    session = _make_session()
    conv = _make_conv()
    conv.created_at = datetime(2026, 8, 30, 10, 0, 0)
    conv.updated_at = datetime(2026, 8, 30, 11, 0, 0)

    list_result = MagicMock()
    list_result.all.return_value = [(conv, 4)]
    count_result = MagicMock()
    count_result.scalar_one.return_value = 1
    session.execute = AsyncMock(side_effect=[list_result, count_result])
    _bind_session(mock_session_cls, session)

    result = await list_conversations("u1", limit=20, offset=0)

    assert result["total"] == 1
    assert len(result["conversations"]) == 1
    item = result["conversations"][0]
    assert item["id"] == "c-1"
    assert item["title"] == "旧会话"
    assert item["message_count"] == 4
    assert item["created_at"] == "2026-08-30T10:00:00"
    assert item["updated_at"] == "2026-08-30T11:00:00"


@patch("app.core.conversation_service.async_session")
async def test_list_conversations_empty(mock_session_cls):
    """无会话：返回空列表与 total=0。"""
    session = _make_session()
    list_result = MagicMock()
    list_result.all.return_value = []
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    session.execute = AsyncMock(side_effect=[list_result, count_result])
    _bind_session(mock_session_cls, session)

    result = await list_conversations("u1")

    assert result == {"conversations": [], "total": 0}
