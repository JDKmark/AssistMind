"""chat SSE 链路 × 服务端会话热态记忆（短期记忆收口）接线测试。

直接驱动 _event_stream（单事件循环，避免 TestClient 跨 loop 的 fakeredis 兼容问题），
覆盖契约：
- 第二轮不带 history → 服务端热态记忆注入（上下文不丢）
- 服务端有记录时客户端伪造 history 不生效（防覆写）
- done 轮次回写热态记忆（下一轮可用）
- 服务端无记录时客户端 history 作种子写回
- Redis 不可用 → 回落请求内 history，流不中断（降级链）
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fakeredis import aioredis as fake_aioredis

from app.api import chat as chat_api
from app.core.dialog.store import DialogStore
from app.core.infra.redis import RedisClient
from app.schemas.chat import ChatRequest


def _make_store() -> DialogStore:
    rc = RedisClient()
    rc._client = fake_aioredis.FakeRedis(decode_responses=True)
    return DialogStore(redis_client=rc)


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


async def _collect_stream(req: ChatRequest, **kwargs) -> list[tuple[str, dict | None]]:
    chunks = []
    async for chunk in chat_api._event_stream(req, **kwargs):
        events = _parse_sse(chunk)
        events = [e for e in events if e[0] and not e[0].startswith("delta")]
        chunks.extend(events)
    return chunks


def _route_chat():
    return AsyncMock(
        return_value={
            "intent": "chat",
            "confidence": 1.0,
            "source": "rule",
            "low_confidence": False,
        }
    )


@pytest.fixture(autouse=True)
def _no_langfuse(monkeypatch):
    monkeypatch.setattr(chat_api, "get_langfuse", lambda: None)


async def test_second_round_without_history_uses_server_memory(monkeypatch):
    """第二轮请求不带 history：服务端热态记忆照常注入 prompt（上下文不断档）。"""
    store = _make_store()
    monkeypatch.setattr(chat_api, "dialog_store", store)
    await store.seed(
        "tester",
        "conv-mem-1",
        [
            {"role": "user", "content": "AssistMind 有学生优惠吗"},
            {"role": "assistant", "content": "有，凭证件 9 折"},
        ],
    )

    captured: dict = {}

    async def _fake_stream(prompt, system=None, generation=False, **kw):
        captured["prompt"] = prompt
        yield "好的"

    req = ChatRequest(query="那怎么申请？", conversation_id="conv-mem-1")
    with patch("app.api.chat.route", new=_route_chat()), patch(
        "app.api.chat.stream_llm", new=_fake_stream
    ):
        events = await _collect_stream(req, role="user", username="tester", user_id="tester")

    assert [e for e, _ in events][-1] == "done"
    assert "AssistMind 有学生优惠吗" in captured["prompt"]
    assert "那怎么申请？" in captured["prompt"]


async def test_forged_client_history_cannot_overwrite_server_memory(monkeypatch):
    """服务端已有记录：客户端回传伪造 history 不生效（S6 信任边界收口）。"""
    store = _make_store()
    monkeypatch.setattr(chat_api, "dialog_store", store)
    await store.seed(
        "tester",
        "conv-mem-2",
        [
            {"role": "user", "content": "服务端真实历史"},
            {"role": "assistant", "content": "好的"},
        ],
    )

    captured: dict = {}

    async def _fake_stream(prompt, system=None, generation=False, **kw):
        captured["prompt"] = prompt
        yield "好的"

    req = ChatRequest(
        query="继续",
        conversation_id="conv-mem-2",
        history=[
            {"role": "user", "content": "伪造历史"},
            {"role": "assistant", "content": "伪造回答"},
        ],
    )
    with patch("app.api.chat.route", new=_route_chat()), patch(
        "app.api.chat.stream_llm", new=_fake_stream
    ):
        await _collect_stream(req, role="user", username="tester", user_id="tester")

    assert "服务端真实历史" in captured["prompt"]
    assert "伪造历史" not in captured["prompt"]


async def test_done_round_appends_to_server_memory(monkeypatch):
    """done 后本轮问答回写热态记忆：下一轮 load 可见。"""
    store = _make_store()
    monkeypatch.setattr(chat_api, "dialog_store", store)

    async def _fake_stream(prompt, system=None, generation=False, **kw):
        yield "这是本轮回答"

    req = ChatRequest(query="你好呀", conversation_id="conv-mem-3")
    with patch("app.api.chat.route", new=_route_chat()), patch(
        "app.api.chat.stream_llm", new=_fake_stream
    ):
        await _collect_stream(req, role="user", username="tester", user_id="tester")

    loaded = await store.load("tester", "conv-mem-3")
    assert loaded == [
        {"role": "user", "content": "你好呀"},
        {"role": "assistant", "content": "这是本轮回答"},
    ]


async def test_client_history_seeds_server_memory(monkeypatch):
    """服务端无记录：客户端 history 生效并作种子写回（换端/续聊场景）。"""
    store = _make_store()
    monkeypatch.setattr(chat_api, "dialog_store", store)

    captured: dict = {}

    async def _fake_stream(prompt, system=None, generation=False, **kw):
        captured["prompt"] = prompt
        yield "好的"

    req = ChatRequest(
        query="接着说",
        conversation_id="conv-mem-4",
        history=[
            {"role": "user", "content": "之前的问题"},
            {"role": "assistant", "content": "之前的回答"},
        ],
    )
    with patch("app.api.chat.route", new=_route_chat()), patch(
        "app.api.chat.stream_llm", new=_fake_stream
    ):
        await _collect_stream(req, role="user", username="tester", user_id="tester")

    assert "之前的问题" in captured["prompt"]
    loaded = await store.load("tester", "conv-mem-4")
    assert loaded == [
        {"role": "user", "content": "之前的问题"},
        {"role": "assistant", "content": "之前的回答"},
        {"role": "user", "content": "接着说"},
        {"role": "assistant", "content": "好的"},
    ]


async def test_redis_down_falls_back_to_request_history(monkeypatch):
    """Redis 不可用：回落请求内 history，流正常完成（降级链不中断）。"""
    # 不注入 fakeredis：chat_api.dialog_store 为未连接 Redis 驱动的真实单例
    captured: dict = {}

    async def _fake_stream(prompt, system=None, generation=False, **kw):
        captured["prompt"] = prompt
        yield "好的"

    req = ChatRequest(
        query="在吗",
        conversation_id="conv-mem-5",
        history=[{"role": "user", "content": "请求内历史"}],
    )
    with patch("app.api.chat.route", new=_route_chat()), patch(
        "app.api.chat.stream_llm", new=_fake_stream
    ):
        events = await _collect_stream(req, role="user", username="tester", user_id="tester")

    assert [e for e, _ in events][-1] == "done"
    assert "请求内历史" in captured["prompt"]


async def test_new_conversation_gets_uuid_and_empty_history(monkeypatch):
    """不带 conversation_id：新建会话（uuid），无历史时 prompt 不含历史段。"""
    store = _make_store()
    monkeypatch.setattr(chat_api, "dialog_store", store)

    captured: dict = {}

    async def _fake_stream(prompt, system=None, generation=False, **kw):
        captured["prompt"] = prompt
        yield "你好"

    req = ChatRequest(query="你好")
    with patch("app.api.chat.route", new=_route_chat()), patch(
        "app.api.chat.stream_llm", new=_fake_stream
    ):
        events = await _collect_stream(req, role="user", username="tester", user_id="tester")

    start = dict(events)["start"]
    assert start["conversation_id"]
    assert captured["prompt"] == "你好"  # 无历史时 prompt 即原话（不拼空历史段）
