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

from app.main import app

client = TestClient(app)


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
    mock_engine = MagicMock()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": [], "degraded": True},
            "contexts": [{"doc_id": "d1", "text": "片段"}],
        }
    )
    mock_engine.generate = AsyncMock(
        return_value={
            "answer": "这是 FAQ 答案",
            "sources": [{"doc_id": "d1", "title": "t"}],
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
        resp = client.post("/api/v1/chat/ask", json={"query": "如何配置系统"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]

    # 序列：start → retrieving → generating → done
    assert names[0] == "start"
    assert "retrieving" in names
    assert "generating" in names
    assert names[-1] == "done"
    assert names.index("retrieving") < names.index("generating")

    ed = _events_dict(events)
    assert ed["start"]["query"] == "如何配置系统"
    assert ed["start"]["intent"] == "faq"
    assert ed["done"]["answer"] == "这是 FAQ 答案"
    assert ed["done"]["sources"][0]["doc_id"] == "d1"

    # retrieve/generate 调用参数正确
    mock_engine.retrieve.assert_awaited_once_with("如何配置系统")
    mock_engine.generate.assert_awaited_once()


# ---------- 2. faq 触发改写发出 rewriting 事件 ----------


def test_chat_faq_rewriting_event():
    """faq 意图且改写有变体（未降级）：验证发出 rewriting 事件且顺序正确。"""
    mock_engine = MagicMock()
    mock_engine.retrieve = AsyncMock(
        return_value={
            "rewrites": {"variants": ["如何配置", "怎么设置"], "degraded": False},
            "contexts": [],
        }
    )
    mock_engine.generate = AsyncMock(return_value={"answer": "A", "sources": []})
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
        resp = client.post("/api/v1/chat/ask", json={"query": "配置"})

    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert "rewriting" in names
    # 顺序：retrieving → rewriting → generating → done
    assert names.index("retrieving") < names.index("rewriting") < names.index("generating")
    rw_data = next(d for e, d in events if e == "rewriting")
    assert rw_data["variants"] == ["如何配置", "怎么设置"]


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
        resp = client.post("/api/v1/chat/ask", json={"query": "创建工单"})

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
            "answer": "您的订单 20240801001 物流轨迹：已揽收。",
            "tool_calls": [
                {
                    "name": "query_logistics",
                    "input": {"order_sn": "20240801001"},
                    "result": [{"ts": "2024-08-01 16:00:00", "content": "已揽收"}],
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
            json={
                "query": "物流到哪了？",
                "history": [
                    {"role": "user", "content": "查一下订单 20240801001"},
                    {"role": "assistant", "content": "您的订单已发货。"},
                ],
            },
        )

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    assert _events_dict(events)["done"]["answer"] == "您的订单 20240801001 物流轨迹：已揽收。"

    # history 完整传入 ToolAgent.run（实体回溯依赖它）
    fake_agent.run.assert_awaited_once()
    call_args = fake_agent.run.await_args
    assert call_args.args[0] == "物流到哪了？"
    assert call_args.kwargs["history"] == [
        {"role": "user", "content": "查一下订单 20240801001"},
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
        resp = client.post("/api/v1/chat/ask", json={"query": "嗯"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names == ["start", "done"]
    done_data = _events_dict(events)["done"]
    # 澄清话术包含意图提示
    assert "意图" in done_data["answer"]


# ---------- 5. chat 意图直接 LLM 对话 ----------


def test_chat_chat_intent_direct_llm():
    """chat 意图：mock call_llm，验证 start → generating → done。"""
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
    ), patch("app.api.chat.call_llm", new=AsyncMock(return_value="你好呀")):
        resp = client.post("/api/v1/chat/ask", json={"query": "你好"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names == ["start", "generating", "done"]
    assert _events_dict(events)["done"]["answer"] == "你好呀"


def test_chat_chat_intent_history_in_prompt():
    """chat 意图带 history：历史以「用户/客服: 内容」格式拼入 LLM prompt。"""
    mock_llm = AsyncMock(return_value="记得啦")
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
    ), patch("app.api.chat.call_llm", new=mock_llm):
        resp = client.post(
            "/api/v1/chat/ask",
            json={
                "query": "那我的订单呢",
                "history": [
                    {"role": "user", "content": "查一下订单 20240801001"},
                    {"role": "assistant", "content": "您的订单已发货。"},
                ],
            },
        )

    assert resp.status_code == 200
    assert _events_dict(_parse_sse(resp.text))["done"]["answer"] == "记得啦"

    # prompt 含历史（用户/客服格式）与当前问题
    prompt = mock_llm.await_args.args[0]
    assert "用户: 查一下订单 20240801001" in prompt
    assert "客服: 您的订单已发货。" in prompt
    assert "那我的订单呢" in prompt


# ---------- 6. error 事件（RAGEngine 抛异常，流已开始）----------


def test_chat_error_event_on_rag_failure():
    """RAGEngine.retrieve 抛异常：start/retrieving 已发，发送 error 事件结束，无 done。"""
    mock_engine = MagicMock()
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
        resp = client.post("/api/v1/chat/ask", json={"query": "如何配置"})

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
        resp = client.post("/api/v1/chat/ask", json={"query": "你好"})

    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names == ["error"]
    assert events[0][1]["message"] == "路由失败"


# ---------- 8. 请求体校验（query 为空 -> 422）----------


def test_chat_validation_empty_query():
    """query 为空：Pydantic min_length=1 校验返回 422，不进入流。"""
    resp = client.post("/api/v1/chat/ask", json={"query": ""})
    assert resp.status_code == 422
