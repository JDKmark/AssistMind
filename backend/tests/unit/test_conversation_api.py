"""会话 API + chat 持久化接线单元测试。

覆盖：
1. GET /api/v1/conversations：200 形状（mock service）+ user_id 透传当前用户名
2. GET /api/v1/conversations：未认证 401
3. GET /api/v1/conversations/{id}/messages：本人 200 / 越权（PermissionError）404 detail="会话不存在"
4. chat ask 接线：请求带 conversation_id → save_round 以该 id 调用（不新建会话语义）
5. chat ask 接线：不带 conversation_id → start/done 回传同一生成的 id，save_round 用之
6. chat ask 接线：save_round 抛异常 → SSE 仍正常 done（持久化失败不阻塞响应）

mock 策略：API 用例 mock app.api.conversation 的 service 引用；chat 接线用例
mock app.api.chat 的 route/call_llm/save_round（同 test_chat_api 模式），不连真实 DB/LLM。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.rag import engine as real_rag_engine
from app.core.security.auth import create_access_token
from app.main import app

client = TestClient(app)


# API 用例统一用 user 角色覆盖鉴权依赖（autouse，结束后恢复，不跨文件污染）
async def fake_user():
    return {"username": "testuser", "role": "user", "access_token": "fake"}


@pytest.fixture(autouse=True)
def _override_auth():
    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = fake_user
    yield
    if original is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = original


@pytest.fixture(autouse=True)
def _disable_langfuse(monkeypatch):
    """chat 接线用例禁用 Langfuse 埋点（no-op），避免读取本地 .env 真实 key。"""
    from app.api import chat as chat_api

    monkeypatch.setattr(chat_api, "get_langfuse", lambda: None)


# ---------- 1. 会话列表 ----------


@patch("app.api.conversation.list_conversations", new_callable=AsyncMock)
def test_list_conversations_success_shape(mock_list):
    """列表 200：返回 {conversations, total}，service 收到 user_id=当前用户名。"""
    mock_list.return_value = {
        "conversations": [
            {
                "id": "c-1",
                "title": "如何配置",
                "user_id": "testuser",
                "created_at": "2026-08-30T10:00:00",
                "updated_at": "2026-08-30T11:00:00",
                "message_count": 2,
            }
        ],
        "total": 1,
    }
    resp = client.get("/api/v1/conversations?limit=10&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["conversations"][0]["id"] == "c-1"
    assert data["conversations"][0]["message_count"] == 2
    mock_list.assert_awaited_once_with(user_id="testuser", limit=10, offset=0)


def test_list_conversations_requires_auth():
    """未认证 401（临时移除鉴权覆盖）。"""
    app.dependency_overrides.pop(get_current_user, None)
    try:
        resp = client.get("/api/v1/conversations")
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    assert resp.status_code == 401


# ---------- 2. 历史消息回看 ----------


@patch("app.api.conversation.list_messages", new_callable=AsyncMock)
def test_list_messages_owner_ok(mock_list):
    """本人会话：200，返回 conversation_id 与消息列表。"""
    mock_list.return_value = {
        "conversation_id": "c-1",
        "messages": [
            {"role": "user", "content": "问题", "intent": "", "created_at": "2026-08-30T10:00:00"},
            {"role": "assistant", "content": "回答", "intent": "faq", "created_at": "2026-08-30T10:00:01"},
        ],
    }
    resp = client.get("/api/v1/conversations/c-1/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_id"] == "c-1"
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    mock_list.assert_awaited_once_with("c-1", user_id="testuser")


@patch("app.api.conversation.list_messages", new_callable=AsyncMock)
def test_list_messages_not_owner_404(mock_list):
    """越权回看：service 抛 PermissionError → 404 detail="会话不存在"（防枚举）。"""
    mock_list.side_effect = PermissionError("会话不存在")
    resp = client.get("/api/v1/conversations/c-other/messages")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "会话不存在"


# ---------- 3. chat 持久化接线 ----------

TEST_TOKEN = create_access_token({"uid": "tester-id", "sub": "testuser", "role": "user"})
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    events: list[tuple[str, dict | None]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event: str | None = None
        data: dict | None = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: "):].strip()
            elif line.startswith("data: "):
                raw = line[len("data: "):]
                data = json.loads(raw) if raw else None
        if event:
            events.append((event, data))
    return events


def _events_dict(events: list[tuple[str, dict | None]]) -> dict[str, dict | None]:
    return {e: d for e, d in events}


def _route_mock(intent: str) -> AsyncMock:
    return AsyncMock(
        return_value={
            "intent": intent,
            "confidence": 1.0,
            "source": "rule",
            "low_confidence": False,
        }
    )


def _stream_llm(text: str):
    """构造 stream_llm 的 async generator mock（一次性 yield 完整文本）。"""
    async def _gen(*args, **kwargs):
        yield text

    return _gen


def _mock_generate_stream(mock_engine, answer: str, sources=None):
    """把 mock_engine.generate_stream 配成流式 async generator（delta + final）。"""
    async def _gen(*args, **kwargs):
        if answer:
            yield {"delta": answer[:2]}
            yield {"delta": answer[2:]}
        yield {"final": {"answer": answer, "sources": sources or [], "degraded": False}}

    mock_engine.generate_stream = _gen


@patch("app.api.chat.save_round", new_callable=AsyncMock)
@patch("app.api.chat.stream_llm", new=_stream_llm("你好呀"))
@patch("app.api.chat.route", new=_route_mock("chat"))
def test_chat_ask_persists_with_request_conversation_id(mock_save):
    """ask 带 conversation_id：save_round 以该 id 调用（归属原会话）。"""
    resp = client.post(
        "/api/v1/chat/ask",
        headers=AUTH_HEADERS,
        json={"query": "你好", "conversation_id": "c-fixed"},
    )
    assert resp.status_code == 200
    names = [e for e, _ in _parse_sse(resp.text)]
    assert names[-1] == "done"
    mock_save.assert_awaited_once_with("c-fixed", "testuser", "你好", "你好呀", intent="chat")


@patch("app.api.chat.save_round", new_callable=AsyncMock)
@patch("app.api.chat.stream_llm", new=_stream_llm("你好呀"))
@patch("app.api.chat.route", new=_route_mock("chat"))
def test_chat_ask_generates_conversation_id_when_absent(mock_save):
    """不带 conversation_id：start/done 回传同一生成的 id，save_round 用它持久化。"""
    resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "你好"})
    assert resp.status_code == 200
    ed = _events_dict(_parse_sse(resp.text))
    cid = ed["start"]["conversation_id"]
    assert cid
    # start/done 回传同一生成的 id（前端两处均消费，done 兜底 start 缺失场景）
    assert ed["done"]["conversation_id"] == cid
    mock_save.assert_awaited_once_with(cid, "testuser", "你好", "你好呀", intent="chat")


@patch("app.api.chat.save_round", new_callable=AsyncMock)
@patch("app.api.chat.stream_llm", new=_stream_llm("回答文本"))
@patch("app.api.chat.route", new=_route_mock("chat"))
def test_chat_save_round_failure_does_not_break_stream(mock_save):
    """save_round 抛异常：SSE 仍正常 done，无 error 事件（持久化失败仅降级）。"""
    mock_save.side_effect = RuntimeError("PG 挂了")
    resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "你好"})
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert "error" not in names
    assert names[-1] == "done"
    assert _events_dict(events)["done"]["answer"] == "回答文本"


@patch("app.api.chat.save_round", new_callable=AsyncMock)
@patch("app.api.chat.rag_engine")
@patch("app.api.chat.route", new=_route_mock("faq"))
def test_chat_faq_persists_round(mock_engine, mock_save):
    """faq 意图 done 路径同样持久化（spec：每轮问答完成入库）。"""
    mock_engine.should_rewrite_retry = real_rag_engine.should_rewrite_retry
    mock_engine.retry_query_for = real_rag_engine.retry_query_for
    mock_engine.no_result_answer = real_rag_engine.no_result_answer
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
            "crag": {"action": "generate", "score": 0.9, "degraded": False},
            "degraded": [],
        }
    )
    _mock_generate_stream(mock_engine, "FAQ 答案", [])
    resp = client.post(
        "/api/v1/chat/ask",
        headers=AUTH_HEADERS,
        json={"query": "如何配置", "conversation_id": "c-faq"},
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert [e for e, _ in events][-1] == "done"
    mock_save.assert_awaited_once_with("c-faq", "testuser", "如何配置", "FAQ 答案", intent="faq")


@patch("app.api.chat.save_round", new_callable=AsyncMock)
@patch("app.api.chat.ToolAgent")
@patch("app.api.chat.route", new=_route_mock("task"))
def test_chat_task_persists_round(mock_agent_cls, mock_save):
    """task 意图 done 路径同样持久化，answer 取 result["answer"]。"""
    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(
        return_value={"answer": "工单已创建", "tool_calls": [], "iterations": 1, "degraded": False}
    )
    mock_agent_cls.return_value = fake_agent
    resp = client.post(
        "/api/v1/chat/ask",
        headers=AUTH_HEADERS,
        json={"query": "创建工单", "conversation_id": "c-task"},
    )
    assert resp.status_code == 200
    assert [e for e, _ in _parse_sse(resp.text)][-1] == "done"
    mock_save.assert_awaited_once_with("c-task", "testuser", "创建工单", "工单已创建", intent="task")
