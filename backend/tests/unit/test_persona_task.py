"""task 意图接入人格单元测试（persona-upgrade T1）。

覆盖：
- 带 persona 的 ToolAgent 跑完整 ReAct 循环（mock LLM，parse_action 真实解析，
  不 mock 解析层）：人格文本不破坏 Action/Action Input 协议、Final Answer 含
  人格语气特征、call_llm 收到的 system_prompt 含 TASK_PERSONA_GUARD
- persona + CLARIFY：追问话术（用户可见文本）体现人格语气
- chat.py 分流接线：persona 非空时 ToolAgent 以
  DEFAULT_SYSTEM_PROMPT → persona_suffix → TASK_PERSONA_GUARD 构造；
  无人格时保持默认构造（不传 system_prompt）

mock 策略与 test_tool_agent.py 一致：
- LLM：patch app.agents.base.call_llm（parse_action 在真实代码路径上执行）
- MCPClient：MagicMock + AsyncMock
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.agents.tool_agent import DEFAULT_SYSTEM_PROMPT, TASK_PERSONA_GUARD, ToolAgent
from app.core.personas import persona_prompt
from app.core.security.auth import create_access_token
from app.main import app

client = TestClient(app)

TEST_TOKEN = create_access_token({"uid": "tester-id", "sub": "tester", "role": "admin"})
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_TOKEN}"}


def _make_mcp_mock(call_tool_return=None, connected=True):
    mcp = MagicMock()
    mcp.is_connected = connected
    mcp.connect = AsyncMock(return_value=connected)
    mcp.call_tool = AsyncMock(
        return_value=call_tool_return if call_tool_return is not None else {}
    )
    mcp.close = AsyncMock()
    return mcp


def _persona_system_prompt() -> str:
    """复现 chat.py _handle_task 的注入公式：DEFAULT → persona → 护栏。"""
    return f"{DEFAULT_SYSTEM_PROMPT}\n{persona_prompt('lively')}\n{TASK_PERSONA_GUARD}"


# ---------- 完整 ReAct 循环（parse_action 真实解析路径）----------


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_task_with_persona_full_react_loop(mock_call):
    """带人格跑完整 ReAct 循环：Action 协议解析不受语气影响，Final Answer 含人格特征。"""
    mcp = _make_mcp_mock(
        call_tool_return={
            "order_sn": "20260801001",
            "status": "已发货",
            "logistics_sn": "SF1234567890",
        }
    )
    # LLM 输出模拟「人格语气渗入 Thought/Final Answer 但协议行保持原样」：
    # parse_action 必须仍能从带语气词的输出中解析出 Action/Action Input
    mock_call.side_effect = [
        'Thought: 好嘞，马上帮您查～\nAction: query_order\nAction Input: {"order_sn": "20260801001"}',
        "Final Answer: 好嘞，您的订单 20260801001 已发货，物流单号 SF1234567890，预计明天送达～",
    ]
    agent = ToolAgent(system_prompt=_persona_system_prompt(), mcp_client=mcp)

    result = await agent.run("查一下订单 20260801001")

    # parse_action 真实解析：工具名与参数正确（人格没有破坏 ReAct 协议）
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "query_order"
    assert result["tool_calls"][0]["input"] == {"order_sn": "20260801001"}
    # Final Answer（用户可见文本）含人格语气特征
    assert "好嘞" in result["answer"]
    assert "20260801001" in result["answer"]
    assert result["degraded"] is False
    # LLM 收到的 system_prompt：人格指令 + 护栏均在，护栏在人格之后
    system_used = mock_call.await_args_list[0].kwargs["system"]
    assert TASK_PERSONA_GUARD in system_used
    assert "活泼" in system_used
    assert system_used.index("活泼") < system_used.index(TASK_PERSONA_GUARD)


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_task_with_persona_clarify_uses_persona_tone(mock_call):
    """persona + 参数缺失：CLARIFY 追问（用户可见文本）体现人格语气，不调工具。"""
    mcp = _make_mcp_mock()
    mock_call.return_value = "CLARIFY: 好嘞，请问您的订单号是多少呀？我马上帮您查～"
    agent = ToolAgent(system_prompt=_persona_system_prompt(), mcp_client=mcp)

    result = await agent.run("我要退货")

    # CLARIFY 经 ToolAgent.parse_action 直接作为最终答案（含人格语气）
    assert "好嘞" in result["answer"]
    assert result["tool_calls"] == []
    assert result["degraded"] is False
    mcp.call_tool.assert_not_awaited()


# ---------- chat.py 分流接线 ----------


def test_chat_api_task_persona_builds_guarded_system_prompt():
    """/ask task 意图带 persona：ToolAgent 以 DEFAULT → persona → 护栏构造。"""
    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(
        return_value={"answer": "好嘞，查到了", "tool_calls": [], "iterations": 1, "degraded": False}
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
    ), patch("app.api.chat.ToolAgent", return_value=fake_agent) as mock_agent_cls, patch(
        "app.api.chat.get_langfuse", lambda: None
    ):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={"query": "查一下订单 20260801001", "persona": "lively"},
        )

    assert resp.status_code == 200
    kwargs = mock_agent_cls.call_args.kwargs
    system_prompt = kwargs["system_prompt"]
    assert system_prompt.startswith(DEFAULT_SYSTEM_PROMPT[:20])  # 既有职责在最前
    assert "活泼" in system_prompt  # 人格语气指令
    assert TASK_PERSONA_GUARD in system_prompt  # 协议护栏
    assert system_prompt.index("活泼") < system_prompt.index(TASK_PERSONA_GUARD)
    fake_agent.run.assert_awaited_once()


def test_chat_api_task_without_persona_keeps_default_construction():
    """task 意图无人格：保持默认构造（不传 system_prompt，行为与现状一致）。"""
    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(
        return_value={"answer": "已创建工单", "tool_calls": [], "iterations": 1, "degraded": False}
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
    ), patch("app.api.chat.ToolAgent", return_value=fake_agent) as mock_agent_cls, patch(
        "app.api.chat.get_langfuse", lambda: None
    ):
        resp = client.post(
            "/api/v1/chat/ask",
            headers=AUTH_HEADERS,
            json={"query": "帮我创建一个工单，标题是登录问题，描述是无法登录"},
        )

    assert resp.status_code == 200
    kwargs = mock_agent_cls.call_args.kwargs
    assert "system_prompt" not in kwargs  # 默认构造，无人格注入
