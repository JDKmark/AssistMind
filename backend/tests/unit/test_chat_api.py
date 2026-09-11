"""Chat API SSE 流式聊天接口单元测试。

覆盖场景：
1. faq 流式事件序列（start/retrieving/done，含 generating 顺序）
2. faq 触发改写时发出 rewriting 事件
3. task 触发 tool_call / tool_result 事件
4. unclear 返回澄清话术
5. chat 意图直接 LLM 对话
6. error 事件（RAGEngine 抛异常，流已开始）
7. error 事件（route 抛异常，start 未发出）
8. 请求体校验（query 为空 -> 422）

mock 策略：mock app.api.chat 中的 route / rag_engine / ToolAgent / call_llm 引用，
不连真实 LLM / 向量库 / MCP。SSE 响应用 TestClient 普通 POST 读取全文后解析。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import pytest

from app.core.rag import engine as real_rag_engine
from app.core.security.auth import create_access_token
from app.main import app

client = TestClient(app)

# chat 接口需登录：所有用例统一带测试 JWT（admin 角色，覆盖 admin-only 管理操作场景）
TEST_TOKEN = create_access_token({"uid": "tester-id", "sub": "tester", "role": "admin"})
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _disable_langfuse(monkeypatch):
    """测试进程会读取 backend/.env 的真实 Langfuse key（此时为启用状态），
    默认禁用 chat trace 埋点（no-op 旁路）；需要验证 trace 的用例自行 patch get_langfuse。
    """
    from app.api import chat as chat_api

    monkeypatch.setattr(chat_api, "get_langfuse", lambda: None)


def _mock_engine() -> MagicMock:
    """构造 RAGEngine mock：CRAG 决策纯函数绑定真实实现（无副作用），仅 IO（retrieve/generate）走 mock。"""
    m = MagicMock()
    m.should_rewrite_retry = real_rag_engine.should_rewrite_retry
    m.retry_query_for = real_rag_engine.retry_query_for
    m.no_result_answer = real_rag_engine.no_result_answer
    return m


class _FakeSpan:
    """langfuse 4.14：trace_id 是属性（非方法）。"""

    def __init__(self, trace_id: str = "trace-fake-1"):
        self.trace_id = trace_id

    def set_trace_io(self, **kwargs):
        pass

    def update(self, **kwargs):
        pass


class _FakeLangfuse:
    """最小 Langfuse 客户端 mock：start_as_current_observation 返回带 trace_id 的 span。"""

    def __init__(self, trace_id: str = "trace-fake-1"):
        self._trace_id = trace_id

    def start_as_current_observation(self, **kwargs):
        from contextlib import contextmanager as _ctx

        @_ctx
        def _body():
            yield _FakeSpan(self._trace_id)

        return _body()


def _mock_generate_stream(
    mock_engine: MagicMock,
    answer: str = "这是 FAQ 答案",
    sources: list | None = None,
    degraded: bool = False,
) -> dict:
    """把 mock_engine.generate_stream 配成流式 async generator（delta + final）。

    generate_stream 是 async generator：chat SSE 链路 `async for` 逐 chunk 消费。
    不能 AsyncMock(side_effect=async_gen)（AsyncMock 会包成 coroutine），
    直接赋普通 async generator 函数，用 calls["count"] 记录调用次数供断言。
    """
    calls: dict = {"count": 0}

    async def _gen(*args, **kwargs):
        calls["count"] += 1
        if answer:
            yield {"delta": answer[:4]}
            yield {"delta": answer[4:]}
        yield {"final": {"answer": answer, "sources": sources or [], "degraded": degraded}}

    mock_engine.generate_stream = _gen
    return calls


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    """解析 SSE 文本为 [(event, data), ...]。

    SSE 事件间以空行（\\n\\n）分隔，每个事件含 `event: <name>` 与 `data: <json>`。
    """
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
    """事件名 -> data（同名事件保留最后一个）。"""
    return {e: d for e, d in events}


# ---------- 1. faq 流式事件序列 ----------


def test_chat_faq_stream_sequence():
    """faq 意图：mock route 返回 faq，mock RAGEngine 返回答案，验证事件序列含 start/retrieving/done。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
            "crag": {"action": "generate", "score": 0.9, "degraded": False},
            "degraded": [],
        }
    )
    gen_calls = _mock_generate_stream(
        mock_engine, "这是 FAQ 答案", [{"doc_id": "d1", "title": "t"}]
    )
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置系统"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]

    # 序列：start → retrieving → generating → delta... → done
    assert names[0] == "start"
    assert "retrieving" in names
    assert "generating" in names
    assert "delta" in names
    assert names[-1] == "done"
    assert names.index("retrieving") < names.index("generating")
    assert names.index("generating") < names.index("delta") < names.index("done")

    ed = _events_dict(events)
    assert ed["start"]["query"] == "如何配置系统"
    assert ed["start"]["intent"] == "faq"
    # delta 累积后与 done.answer 一致（打字机 + 兜底）
    delta_text = "".join(d["delta"] for e, d in events if e == "delta")
    assert delta_text == "这是 FAQ 答案"
    assert ed["done"]["answer"] == "这是 FAQ 答案"
    assert ed["done"]["sources"][0]["doc_id"] == "d1"
    # 追溯快照：done 回传 CRAG 决策与降级项（前端随反馈提交入库）
    assert ed["done"]["crag_action"] == "generate"
    assert ed["done"]["degraded"] == []

    # retrieve/generate_stream 调用参数正确；登录角色（admin）透传进 faq 检索
    mock_engine.retrieve.assert_awaited_once_with("如何配置系统", role="admin")
    assert gen_calls["count"] == 1


# ---------- 1c. faq done 诊断字段（管理员定位问题用）----------


def test_chat_faq_done_carries_diagnostics():
    """faq done 携带诊断字段：start.role（RBAC 上下文）、done.crag_score 与 timings 阶段耗时。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
            "crag": {"action": "generate", "score": 0.92, "degraded": False},
            "degraded": [],
        }
    )
    _mock_generate_stream(mock_engine, "这是 FAQ 答案", [{"doc_id": "d1", "title": "t"}])
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置系统"})

    assert resp.status_code == 200
    ed = _events_dict(_parse_sse(resp.text))

    # start 事件携带实际用于检索的 RBAC 角色（admin 透传）
    assert ed["start"]["role"] == "admin"

    # done 携带 CRAG 评估分数与后端计时（检索/生成/总耗时齐全）
    assert ed["done"]["crag_score"] == 0.92
    timings = ed["done"]["timings"]
    assert {"total_ms", "retrieve_ms", "generate_ms"} == set(timings.keys())
    assert timings["total_ms"] >= timings["retrieve_ms"]


def test_chat_faq_no_result_done_carries_timings():
    """faq 短路路径（no_result）：done 仍携带 total_ms/retrieve_ms，便于定位"未找到"慢在哪。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [],
            "crag": {"action": "no_result", "score": 0.1, "degraded": False},
            "degraded": [],
        }
    )
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置系统"})

    timings = _events_dict(_parse_sse(resp.text))["done"]["timings"]
    assert "total_ms" in timings
    assert "retrieve_ms" in timings
    assert timings["retrieve_ms"] <= timings["total_ms"]


# ---------- 1b. faq 空检索：CRAG no_result 门禁（防空检索幻觉）----------


def test_chat_faq_no_result_returns_fallback():
    """faq 意图且检索结果为空（CRAG no_result）：直接 done 返回"未找到"，不调用 generate。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [],
            "crag": {"action": "no_result", "score": 0.1, "degraded": False},
            "degraded": [],
        }
    )
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置系统"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    # 不经过 generating（空检索直接短路）
    assert "generating" not in names
    assert names[-1] == "done"

    done_data = _events_dict(events)["done"]
    assert "未找到相关文档" in done_data["answer"]
    assert "如何配置系统" in done_data["answer"]
    assert done_data["sources"] == []
    # 空检索不带着空上下文生成
    mock_engine.generate_stream.assert_not_called()


def test_chat_faq_rewrite_retry_empty_recheck():
    """faq 意图且 CRAG rewrite_retry 二次检索后仍为空：复检后走 no_result，不再生成。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        side_effect=[
            # 首次：低分触发重检索
            {
                "rewrites": {"variants": ["如何配置系统 配置步骤"], "degraded": False},
                "contexts": [{"doc_id": "d1", "text": "弱相关片段"}],
                "crag": {"action": "rewrite_retry", "score": 0.5, "degraded": False},
                "degraded": [],
            },
            # 二次（重检索）：仍为空
            {
                "rewrites": {"variants": [], "degraded": False},
                "contexts": [],
                "crag": {"action": "no_result", "score": 0.1, "degraded": False},
                "degraded": [],
            },
        ]
    )
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置系统"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    # 发出 rewriting 事件（低分被动改写），但重检索仍空 → 短路 no_result，不生成
    assert "rewriting" in names
    assert "generating" not in names
    assert names[-1] == "done"
    assert "未找到相关文档" in _events_dict(events)["done"]["answer"]
    mock_engine.generate_stream.assert_not_called()
    # 二次检索用改写变体
    mock_engine.retrieve.assert_awaited_with("如何配置系统 配置步骤", role="admin")


# ---------- 2. faq 触发改写发出 rewriting 事件 ----------


def test_chat_faq_rewriting_event():
    """faq 意图且 CRAG rewrite_retry（改写未降级）：验证发出 rewriting 事件且顺序正确。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        side_effect=[
            {
                "rewrites": {"variants": ["如何配置", "怎么设置"], "degraded": False},
                "contexts": [{"doc_id": "d1", "text": "弱相关"}],
                "crag": {"action": "rewrite_retry", "score": 0.5, "degraded": False},
                "degraded": [],
            },
            {
                "rewrites": {"variants": [], "degraded": False},
                "contexts": [{"doc_id": "d1", "text": "强相关片段"}],
                "crag": {"action": "generate", "score": 0.9, "degraded": False},
                "degraded": [],
            },
        ]
    )
    _mock_generate_stream(mock_engine, "A", [{"doc_id": "d1", "title": "t"}])
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "配置"})

    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert "rewriting" in names
    # 顺序：retrieving → rewriting → generating → done
    assert names.index("retrieving") < names.index("rewriting") < names.index("generating")
    rw_data = next(d for e, d in events if e == "rewriting")
    assert rw_data["variants"] == ["如何配置", "怎么设置"]
    # 二次检索使用改写变体
    mock_engine.retrieve.assert_awaited_with("如何配置", role="admin")
    assert mock_engine.retrieve.await_count == 2


# ---------- 3. task 触发 tool_call / tool_result 事件 ----------


def test_chat_task_tool_call_events():
    """task 意图：mock ToolAgent 返回含 tool_calls，验证事件含 tool_call/tool_result。"""
    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(
        return_value={
            "answer": "工单已创建",
            "tool_calls": [
                {
                    "name": "create_ticket",
                    "input": {"title": "T"},
                    "result": {"ticket_id": "TK-1"},
                },
                {
                    "name": "get_ticket_status",
                    "input": {"ticket_id": "TK-1"},
                    "result": {"status": "open"},
                },
            ],
            "iterations": 2,
            "degraded": False,
        }
    )
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "task",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.ToolAgent", return_value=fake_agent):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "创建工单"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]

    # 两轮工具调用
    assert names.count("tool_call") == 2
    assert names.count("tool_result") == 2
    # tool_call 在对应 tool_result 之前，最后为 done
    assert names.index("tool_call") < names.index("tool_result")
    assert names[-1] == "done"
    assert names[0] == "start"

    # 首个 tool_call 内容正确
    first_tool_call = next(d for e, d in events if e == "tool_call")
    assert first_tool_call["tool_name"] == "create_ticket"
    assert first_tool_call["arguments"] == {"title": "T"}
    first_tool_result = next(d for e, d in events if e == "tool_result")
    assert first_tool_result["tool_name"] == "create_ticket"
    assert first_tool_result["result"] == {"ticket_id": "TK-1"}

    # done 携带最终答案
    assert _events_dict(events)["done"]["answer"] == "工单已创建"

    # ToolAgent 实例化一次，run 调用一次（无 history 时传 None）
    fake_agent.run.assert_awaited_once_with("创建工单", history=None)


# ---------- 3b. task 意图多轮 history 传递 ----------


def test_chat_task_passes_history():
    """task 意图带 history：完整传入 ToolAgent.run，超出记忆窗口时裁剪。"""
    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(
        return_value={
            "answer": "您的订单 20260801001 物流轨迹：已揽收。",
            "tool_calls": [
                {
                    "name": "query_logistics",
                    "input": {"order_sn": "20260801001"},
                    "result": [{"ts": "2026-08-01 16:00:00", "content": "已揽收"}],
                },
            ],
            "iterations": 1,
            "degraded": False,
        }
    )
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "task",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.ToolAgent", return_value=fake_agent):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={
                "query": "物流到哪了？",
                "history": [
                    {"role": "user", "content": "查一下订单 20260801001"},
                    {"role": "assistant", "content": "您的订单已发货。"},
                ],
            },
        )

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert _events_dict(events)["done"]["answer"] == "您的订单 20260801001 物流轨迹：已揽收。"

    # history 完整传入 ToolAgent.run（实体回溯依赖它）
    fake_agent.run.assert_awaited_once()
    call_args = fake_agent.run.await_args
    assert call_args.args[0] == "物流到哪了？"
    assert call_args.kwargs["history"] == [
        {"role": "user", "content": "查一下订单 20260801001"},
        {"role": "assistant", "content": "您的订单已发货。"},
    ]


def test_chat_task_history_truncated_to_memory_window(monkeypatch):
    """history 超过 MEMORY_WINDOW 时只保留最近 N 条（与 faq 同一窗口语义）。

    裁剪逻辑已集中到 DialogManager（core/dialog），此处通过 dialog 模块的
    settings 控制窗口，验证 API 层仍按统一语义裁剪。
    """
    from app.core.dialog import manager as dialog_manager

    monkeypatch.setattr(dialog_manager.settings, "MEMORY_WINDOW", 2)

    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(return_value={"answer": "ok", "tool_calls": [], "iterations": 0, "degraded": False})
    with patch(
        "app.api.chat.route",
        new=AsyncMock(return_value={"intent": "task", "confidence": 1.0, "source": "rule", "low_confidence": False}),
    ), patch("app.api.chat.ToolAgent", return_value=fake_agent):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={
                "query": "查订单",
                "history": [
                    {"role": "user", "content": f"第{i}条" if i < 4 else f"第{i}条"}
                    for i in range(1, 5)
                ],
            },
        )

    assert resp.status_code == 200
    sent = fake_agent.run.await_args.kwargs["history"]
    assert len(sent) == 2
    # 保留的是最近两条
    assert sent[0]["content"] == "第3条"
    assert sent[1]["content"] == "第4条"


# ---------- 4. unclear 返回澄清话术 ----------


def test_chat_unclear_returns_clarification():
    """unclear 意图：done 事件答案为澄清话术，且序列仅 start → done。"""
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "unclear",
                "confidence": 0.0,
                "source": "fallback",
                "low_confidence": True,
            }
        ),
    ):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "嗯"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names == ["start", "done"]
    done_data = _events_dict(events)["done"]
    # 澄清话术包含意图提示
    assert "意图" in done_data["answer"]


# ---------- 5. chat 意图直接 LLM 对话 ----------


def _stream_llm_mock(answer: str) -> tuple[list, object]:
    """构造 stream_llm 的 async generator mock，并记录调用参数。

    stream_llm 是 async generator（逐 chunk yield），调用返回 generator 而非被 await，
    故用 calls 列表记录 (args, kwargs)，与旧 call_llm 的 await_args 语义等价。
    """
    calls: list = []

    async def _gen(*args, **kwargs):
        calls.append((args, kwargs))
        # 拆成多个 chunk 模拟打字机
        for i in range(0, len(answer), 2):
            yield answer[i : i + 2]

    return calls, _gen


def test_chat_chat_intent_direct_llm():
    """chat 意图：mock stream_llm，验证 start → generating → delta → done。"""
    calls, gen = _stream_llm_mock("你好呀")
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "chat",
                "confidence": 0.9,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.stream_llm", side_effect=gen):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "你好"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names[0] == "start"
    assert "generating" in names
    assert "delta" in names
    assert names[-1] == "done"
    # delta 累积等于完整答案
    delta_text = "".join(d["delta"] for e, d in events if e == "delta")
    assert delta_text == "你好呀"
    assert _events_dict(events)["done"]["answer"] == "你好呀"


def test_chat_chat_intent_history_in_prompt():
    """chat 意图带 history：历史以「用户/客服: 内容」格式拼入 LLM prompt。"""
    calls, gen = _stream_llm_mock("记得啦")
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "chat",
                "confidence": 0.9,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.stream_llm", side_effect=gen):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={
                "query": "那我的订单呢",
                "history": [
                    {"role": "user", "content": "查一下订单 20260801001"},
                    {"role": "assistant", "content": "您的订单已发货。"},
                ],
            },
        )

    assert resp.status_code == 200
    assert _events_dict(_parse_sse(resp.text))["done"]["answer"] == "记得啦"

    # prompt 含历史（用户/客服格式）与当前问题
    prompt = calls[-1][0][0]
    assert "用户: 查一下订单 20260801001" in prompt
    assert "客服: 您的订单已发货。" in prompt
    assert "那我的订单呢" in prompt


# ---------- 5.1 人格（角色语气定制）----------


def test_chat_personas_endpoint_returns_list():
    """/chat/personas：返回 id/name/description 列表，不含 system_prompt。"""
    resp = client.get("/api/v1/chat/personas", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["personas"]) == 3
    for item in data["personas"]:
        assert set(item.keys()) == {"id", "name", "description"}


def test_chat_personas_requires_auth():
    """/chat/personas 未认证：401。"""
    resp = client.get("/api/v1/chat/personas")
    assert resp.status_code == 401


def test_chat_intent_with_persona_appends_style_to_system():
    """chat 意图带 persona：人格语气指令拼在 _CHAT_SYSTEM 之后（不覆盖职责）。"""
    calls, gen = _stream_llm_mock("好嘞，马上帮您看")
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "chat",
                "confidence": 0.9,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.stream_llm", side_effect=gen):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={"query": "你好", "persona": "lively"},
        )

    assert resp.status_code == 200
    assert _events_dict(_parse_sse(resp.text))["done"]["answer"] == "好嘞，马上帮您看"
    system = calls[-1][1]["system"]
    assert system.startswith("你是 AssistMind 智能客服")
    assert "活泼" in system  # 人格语气指令已追加


def test_chat_intent_invalid_persona_falls_back_to_default(caplog):
    """chat 意图带未知 persona：warning 回落默认语气，不 500。"""
    calls, gen = _stream_llm_mock("您好")
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "chat",
                "confidence": 0.9,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.stream_llm", side_effect=gen):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={"query": "你好", "persona": "ghost"},
        )

    assert resp.status_code == 200
    system = calls[-1][1]["system"]
    assert system == "你是 AssistMind 智能客服，请友好、简洁地与用户对话。"
    assert any("persona" in r.message for r in caplog.records)


# ---------- 5.2 FAQ 语义缓存接线（高频问题秒回；故障仅降级命中率）----------


def test_faq_cache_hit_skips_retrieval():
    """faq 缓存命中：直接 done（带 from_cache），不调用 retrieve/generate_stream。"""
    mock_engine = _mock_engine()
    async def _never(*args, **kwargs):
        raise AssertionError("缓存命中不应触发检索")
        yield  # pragma: no cover

    mock_engine.retrieve = AsyncMock(side_effect=AssertionError("不应检索"))
    mock_engine.generate_stream = _never
    cached = {"answer": "缓存答案", "sources": [{"doc_id": "d1"}], "from_cache": "L1"}
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine), patch(
        "app.api.chat.semantic_cache.get", new=AsyncMock(return_value=cached)
    ):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "华为 Mate 70 Pro 多少钱"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    # 命中路径：start → retrieving → done（无 generating / delta / 检索）
    assert names == ["start", "retrieving", "done"]
    done_data = _events_dict(events)["done"]
    assert done_data["answer"] == "缓存答案"
    assert done_data["sources"] == [{"doc_id": "d1"}]
    assert done_data["from_cache"] == "L1"
    mock_engine.retrieve.assert_not_called()


def test_faq_cache_miss_then_writes():
    """faq 缓存未命中：正常检索+生成（流式），完成后写入缓存（含 role）。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
            "crag": {"action": "generate", "score": 0.9, "degraded": False},
            "degraded": [],
        }
    )
    _mock_generate_stream(mock_engine, "最新答案", [{"doc_id": "d1", "title": "t"}])
    mock_set = AsyncMock()
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine), patch(
        "app.api.chat.semantic_cache.get", new=AsyncMock(return_value=None)
    ), patch("app.api.chat.semantic_cache.set", new=mock_set):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={"query": "华为 Mate 70 Pro 多少钱"},
        )

    assert resp.status_code == 200
    ed = _events_dict(_parse_sse(resp.text))
    assert ed["done"]["answer"] == "最新答案"
    # 以登录角色（admin）写入缓存（RBAC role 隔离）；无 persona 落 default 桶
    mock_set.assert_awaited_once_with(
        "华为 Mate 70 Pro 多少钱", "最新答案", [{"doc_id": "d1", "title": "t"}], role="admin", persona=""
    )


def test_faq_persona_uses_persona_bucket_cache():
    """faq 带 persona：缓存照常查/写，按 persona 分桶（选人格不再放弃缓存）。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
            "crag": {"action": "generate", "score": 0.9, "degraded": False},
            "degraded": [],
        }
    )
    _mock_generate_stream(mock_engine, "带人格的回答", [])
    mock_get = AsyncMock(return_value=None)
    mock_set = AsyncMock()
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine), patch(
        "app.api.chat.semantic_cache.get", new=mock_get
    ), patch("app.api.chat.semantic_cache.set", new=mock_set):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={"query": "华为 Mate 70 Pro 多少钱", "persona": "lively"},
        )

    assert resp.status_code == 200
    assert _events_dict(_parse_sse(resp.text))["done"]["answer"] == "带人格的回答"
    # 缓存查/写均带原始 persona id（lively 桶），与 default / 其他人格互不串桶
    mock_get.assert_awaited_once_with("华为 Mate 70 Pro 多少钱", role="admin", persona="lively")
    mock_set.assert_awaited_once_with(
        "华为 Mate 70 Pro 多少钱", "带人格的回答", [], role="admin", persona="lively"
    )


# ---------- 6. error 事件（RAGEngine 抛异常，流已开始）----------


def test_chat_error_event_on_rag_failure():
    """RAGEngine.retrieve 抛异常：start/retrieving 已发，发送 error 事件结束，无 done。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(side_effect=RuntimeError("向量库挂了"))
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置"})

    # 流已开始，status 仍为 200，错误以 error 事件返回
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert "error" in names
    assert names[-1] == "error"
    # start 与 retrieving 已发出
    assert "start" in names
    assert "retrieving" in names
    # 不应出现 done
    assert "done" not in names
    assert _events_dict(events)["error"]["message"] == "向量库挂了"


# ---------- 7. error 事件（route 抛异常，start 未发出）----------


def test_chat_error_event_on_route_failure():
    """route 抛异常（start 未发出）：仅发送 error 事件。"""
    with patch(
        "app.api.chat.route",
        new=AsyncMock(side_effect=RuntimeError("路由失败")),
    ):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "你好"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names == ["error"]
    assert events[0][1]["message"] == "路由失败"


# ---------- 8. 请求体校验（query 为空 -> 422）----------


def test_chat_validation_empty_query():
    """query 为空：Pydantic min_length=1 校验返回 422，不进入流。"""
    resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": ""})
    assert resp.status_code == 422


# ---------- 9. 鉴权 ----------


def test_chat_requires_auth():
    """无 token 访问 chat：401，SSE 流不启动。"""
    resp = client.post("/api/v1/chat/ask", json={"query": "如何配置系统"})
    assert resp.status_code == 401


def test_chat_rejects_invalid_token():
    """非法 token 访问 chat：401。"""
    resp = client.post(
        "/api/v1/chat/ask",
        headers={"Authorization": "Bearer not-a-real-token"},
        json={"query": "如何配置系统"},
    )
    assert resp.status_code == 401


# ---------- 10. faq done 携带 conversation_id / Langfuse trace_id（Bad Case 归因）----------


def test_chat_faq_done_carries_conversation_id():
    """faq done 事件携带 conversation_id；Langfuse 未启用时 trace_id 为空且全程 no-op。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
            "crag": {"action": "generate", "score": 0.9, "degraded": False},
            "degraded": [],
        }
    )
    _mock_generate_stream(mock_engine, "A", [])
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置"})

    events = _parse_sse(resp.text)
    ed = _events_dict(events)
    # start 事件带会话 ID
    assert ed["start"]["conversation_id"]
    # done 事件带同一条会话 ID；未启用 Langfuse 时 trace_id 为空
    assert ed["done"]["conversation_id"] == ed["start"]["conversation_id"]
    assert ed["done"]["trace_id"] == ""


def test_chat_faq_enabled_langfuse_passes_trace_id():
    """Langfuse 启用时：chat_faq 根 trace 创建，done 事件回传 trace_id。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
            "crag": {"action": "generate", "score": 0.9, "degraded": False},
            "degraded": [],
        }
    )
    _mock_generate_stream(mock_engine, "A", [])
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.rag_engine", new=mock_engine), patch(
        "app.api.chat.get_langfuse", return_value=_FakeLangfuse("trace-faq-1")
    ):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "如何配置"})

    done = _events_dict(_parse_sse(resp.text))["done"]
    assert done["trace_id"] == "trace-faq-1"


# ---------- 10b. trace_id 全链路覆盖（缓存命中 / task / chat 均回传）----------
#
# 诊断面板上"Trace ID 为空"的根因：缓存命中分支与 task/chat 意图的 done 事件
# 都不回传 trace_id。以下用例锁定三条路径都必须携带 trace_id（Langfuse 启用时）。


def test_faq_cache_hit_done_carries_trace_id():
    """faq 缓存命中：done 既带 from_cache，也带 trace_id（不再是空 Trace ID）。"""
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "faq",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch(
        "app.api.chat.get_langfuse", return_value=_FakeLangfuse("trace-cache-1")
    ), patch(
        "app.api.chat.semantic_cache.get",
        new=AsyncMock(
            return_value={
                "answer": "缓存答案",
                "sources": [{"doc_id": "d1"}],
                "from_cache": "L1",
            }
        ),
    ):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "华为多少钱"})

    done = _events_dict(_parse_sse(resp.text))["done"]
    assert done["from_cache"] == "L1"
    assert done["trace_id"] == "trace-cache-1"
    assert done["crag_action"] == ""


def test_chat_task_done_carries_trace_id_when_langfuse_enabled():
    """task 意图：整条链路包在 chat_task trace 里，done 回传 trace_id 与 conversation_id。"""
    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(
        return_value={
            "answer": "工单已创建",
            "tool_calls": [
                {
                    "name": "create_ticket",
                    "input": {"title": "T"},
                    "result": {"ticket_id": "TK-1"},
                }
            ],
            "iterations": 1,
            "degraded": False,
        }
    )
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "task",
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch(
        "app.api.chat.get_langfuse", return_value=_FakeLangfuse("trace-task-1")
    ), patch("app.api.chat.ToolAgent", return_value=fake_agent):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "创建工单"})

    ed = _events_dict(_parse_sse(resp.text))
    done = ed["done"]
    assert done["answer"] == "工单已创建"
    assert done["trace_id"] == "trace-task-1"
    assert done["conversation_id"] == ed["start"]["conversation_id"]


def test_chat_chat_intent_done_carries_trace_id_without_langfuse_keeps_empty():
    """chat 意图：Langfuse 未启用（默认 fixture）时 done.trace_id 为空串（no-op 不埋点）。"""
    calls, gen = _stream_llm_mock("你好呀")
    with patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": "chat",
                "confidence": 0.9,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    ), patch("app.api.chat.stream_llm", side_effect=gen):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "你好"})

    done = _events_dict(_parse_sse(resp.text))["done"]
    assert done["answer"] == "你好呀"
    assert done["trace_id"] == ""


# ---------- phase15：产品消歧接线 ----------

# 消歧管道返回值（契约见 spec 第 5 章；测试用 mock 固定形状）
_DISAMBIG_CLARIFY = {
    "status": "clarify",
    "method": "clarify",
    "product_id": None,
    "product_name": None,
    "annotated_query": "我的 S1 Pro 不吸了",
    "confirm_hint": False,
    "candidates": [
        {
            "product_id": "P006",
            "name": "贝亲 S1 Pro 电动吸奶器",
            "spec": "双边电动 静音款",
            "score": 0,
            "in_orders": True,
        },
        {
            "product_id": "P007",
            "name": "追觅 S1 Pro 扫地机器人",
            "spec": "自集尘 拖扫一体",
            "score": 0,
            "in_orders": True,
        },
    ],
    "clarify_text": (
        "S1 Pro 有两款产品：贝亲 S1 Pro 电动吸奶器（双边电动 静音款）"
        "和追觅 S1 Pro 扫地机器人（自集尘 拖扫一体）。请问您说的是哪一款？"
    ),
}

_DISAMBIG_RESOLVED = {
    "status": "resolved",
    "method": "orders",
    "product_id": "P006",
    "product_name": "贝亲 S1 Pro 电动吸奶器",
    "annotated_query": "我的 S1 Pro 不吸了 贝亲 S1 Pro 电动吸奶器",
    "confirm_hint": True,
    "candidates": [
        {
            "product_id": "P006",
            "name": "贝亲 S1 Pro 电动吸奶器",
            "spec": "双边电动 静音款",
            "score": 0,
            "in_orders": True,
        },
        {
            "product_id": "P007",
            "name": "追觅 S1 Pro 扫地机器人",
            "spec": "自集尘 拖扫一体",
            "score": 0,
            "in_orders": True,
        },
    ],
    "clarify_text": "",
}


def _disambig_route_patch(intent: str):
    return patch(
        "app.api.chat.route",
        new=AsyncMock(
            return_value={
                "intent": intent,
                "confidence": 1.0,
                "source": "rule",
                "low_confidence": False,
            }
        ),
    )


def test_chat_faq_disambiguation_clarify_short_circuits():
    """clarify 短路：事件序列 start → disambiguation → done，无检索/生成，LLM 零调用。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock()
    gen_mock = MagicMock()
    mock_engine.generate_stream = gen_mock
    disamb_mock = AsyncMock(return_value=_DISAMBIG_CLARIFY)
    with _disambig_route_patch("faq"), patch(
        "app.api.chat.disambiguate_product", new=disamb_mock
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post(
            "/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "我的 S1 Pro 不吸了"}
        )

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names == ["start", "disambiguation", "done"]

    ed = _events_dict(events)
    assert ed["disambiguation"]["status"] == "clarify"
    assert [c["product_id"] for c in ed["disambiguation"]["candidates"]] == ["P006", "P007"]
    assert ed["done"]["answer"] == _DISAMBIG_CLARIFY["clarify_text"]
    assert ed["done"]["conversation_id"]
    assert ed["done"]["disambiguation"]["status"] == "clarify"
    assert ed["done"]["disambiguation"]["candidates"][0]["name"] == "贝亲 S1 Pro 电动吸奶器"

    # 短路语义：检索与生成零调用
    mock_engine.retrieve.assert_not_awaited()
    gen_mock.assert_not_called()


def test_chat_faq_disambiguation_receives_requester_identity():
    """消歧以登录身份查询订单层：requester_user_id / requester_username 来自 JWT。"""
    disamb_mock = AsyncMock(return_value=_DISAMBIG_RESOLVED)
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
            "crag": {"action": "generate", "score": 0.9, "degraded": False},
            "degraded": [],
        }
    )
    _mock_generate_stream(mock_engine, "这是答案")
    with _disambig_route_patch("faq"), patch(
        "app.api.chat.disambiguate_product", new=disamb_mock
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        client.post(
            "/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "我的 S1 Pro 不吸了"}
        )
    assert disamb_mock.await_args.kwargs["requester_user_id"] == "tester-id"
    assert disamb_mock.await_args.kwargs["requester_username"] == "tester"


def test_chat_faq_resolved_uses_annotated_query_and_confirm_hint():
    """resolved：disambiguation 事件字段齐全；检索/生成收到 annotated_query；system 注入确认指令。"""
    mock_engine = _mock_engine()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
            "crag": {"action": "generate", "score": 0.9, "degraded": False},
            "degraded": [],
        }
    )
    gen_kwargs: dict = {}

    async def _gen(query, contexts, history, system_suffix=None):
        gen_kwargs["query"] = query
        gen_kwargs["system_suffix"] = system_suffix
        yield {"delta": "答案"}
        yield {"final": {"answer": "答案", "sources": [], "degraded": False}}

    mock_engine.generate_stream = _gen
    disamb_mock = AsyncMock(return_value=_DISAMBIG_RESOLVED)
    with _disambig_route_patch("faq"), patch(
        "app.api.chat.disambiguate_product", new=disamb_mock
    ), patch("app.api.chat.rag_engine", new=mock_engine):
        resp = client.post(
            "/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "我的 S1 Pro 不吸了"}
        )

    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    # 消歧事件在 start 之后、retrieving 之前
    assert names.index("disambiguation") > names.index("start")
    assert names.index("disambiguation") < names.index("retrieving")

    ed = _events_dict(events)
    assert ed["disambiguation"] == {
        "status": "resolved",
        "method": "orders",
        "product_id": "P006",
        "product_name": "贝亲 S1 Pro 电动吸奶器",
    }

    # 检索与生成均以注记 query 进行
    mock_engine.retrieve.assert_awaited_once_with(
        "我的 S1 Pro 不吸了 贝亲 S1 Pro 电动吸奶器", role="admin"
    )
    assert gen_kwargs["query"] == "我的 S1 Pro 不吸了 贝亲 S1 Pro 电动吸奶器"
    # system 追加产品确认指令
    assert "贝亲 S1 Pro 电动吸奶器" in (gen_kwargs["system_suffix"] or "")


def test_chat_task_resolved_passes_annotated_query_and_preset_entities():
    """task resolved：Agent 收到 annotated_query 与 preset_entities（显式抽取优先在 Agent 内保证）。"""
    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(
        return_value={"answer": "已确认产品", "tool_calls": [], "iterations": 1}
    )
    disamb_mock = AsyncMock(return_value=_DISAMBIG_RESOLVED)
    with _disambig_route_patch("task"), patch(
        "app.api.chat.disambiguate_product", new=disamb_mock
    ), patch("app.api.chat.ToolAgent", return_value=fake_agent):
        resp = client.post(
            "/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "我的 S1 Pro 不吸了"}
        )

    assert resp.status_code == 200
    names = [e for e, _ in _parse_sse(resp.text)]
    assert "disambiguation" in names
    assert names[-1] == "done"
    fake_agent.run.assert_awaited_once_with(
        "我的 S1 Pro 不吸了 贝亲 S1 Pro 电动吸奶器",
        history=None,
        preset_entities={"product_id": "P006"},
    )


def test_chat_task_disambiguation_clarify_short_circuits_before_agent():
    """task clarify 短路：不构造 Agent，事件序列 start → disambiguation → done。"""
    fake_agent_cls = MagicMock()
    disamb_mock = AsyncMock(return_value=_DISAMBIG_CLARIFY)
    with _disambig_route_patch("task"), patch(
        "app.api.chat.disambiguate_product", new=disamb_mock
    ), patch("app.api.chat.ToolAgent", new=fake_agent_cls):
        resp = client.post(
            "/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "我的 S1 Pro 不吸了"}
        )

    names = [e for e, _ in _parse_sse(resp.text)]
    assert names == ["start", "disambiguation", "done"]
    fake_agent_cls.assert_not_called()


def test_chat_intent_skips_disambiguation():
    """chat 意图不触发消歧：无 disambiguation 事件，消歧函数零调用。"""
    calls, gen = _stream_llm_mock("你好呀")
    disamb_mock = AsyncMock()
    with _disambig_route_patch("chat"), patch(
        "app.api.chat.disambiguate_product", new=disamb_mock
    ), patch("app.api.chat.stream_llm", side_effect=gen):
        resp = client.post("/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "你好"})

    names = [e for e, _ in _parse_sse(resp.text)]
    assert "disambiguation" not in names
    disamb_mock.assert_not_awaited()


def test_unclear_intent_skips_disambiguation():
    """unclear 意图不触发消歧：无 disambiguation 事件，消歧函数零调用。"""
    disamb_mock = AsyncMock()
    with _disambig_route_patch("unclear"), patch(
        "app.api.chat.disambiguate_product", new=disamb_mock
    ):
        resp = client.post(
            "/api/v1/chat/ask", headers=AUTH_HEADERS, json={"query": "嗯嗯嗯？？"}
        )

    names = [e for e, _ in _parse_sse(resp.text)]
    assert "disambiguation" not in names
    assert names[-1] == "done"
    disamb_mock.assert_not_awaited()

