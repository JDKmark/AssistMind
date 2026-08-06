"""BaseReActAgent 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage

from app.agents.base import AgentState, BaseReActAgent
from app.core.infra.llm_factory import LLMUnavailableError


class TestAgent(BaseReActAgent):
    """测试用 Agent 子类。"""

    def get_tools(self):
        return [
            {"name": "search", "description": "搜索", "input_schema": {"query": "str"}}
        ]

    async def execute_tool(self, name, input_data):
        if name == "search":
            return {"result": "搜索结果"}
        return {"error": "未知工具"}


class FailingToolAgent(BaseReActAgent):
    """工具执行抛异常的 Agent。"""

    def get_tools(self):
        return [
            {"name": "search", "description": "搜索", "input_schema": {"query": "str"}}
        ]

    async def execute_tool(self, name, input_data):
        raise RuntimeError("工具执行失败")


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_normal_loop(mock_call):
    """正常循环：先调用工具，再给出最终答案。"""
    mock_call.side_effect = [
        'Action: search\nAction Input: {"query":"test"}',
        "Final Answer: 答案是...",
    ]
    agent = TestAgent()
    result = await agent.run("测试问题")

    assert result["answer"] == "答案是..."
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "search"
    assert result["tool_calls"][0]["result"] == {"result": "搜索结果"}
    assert result["iterations"] == 2
    assert result["degraded"] is False


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_max_iterations_break(mock_call):
    """达上限中断：一直调工具不给最终答案，Loop Breaker 触发。"""
    mock_call.return_value = 'Action: search\nAction Input: {"query":"test"}'
    agent = TestAgent()
    assert agent.max_iterations == 5

    result = await agent.run("测试问题")

    assert result["iterations"] == 5
    assert result["degraded"] is True
    assert "无法处理" in result["answer"]


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_direct_answer(mock_call):
    """直接给出答案：不调用工具。"""
    mock_call.return_value = "Final Answer: 直接答案"
    agent = TestAgent()

    result = await agent.run("测试问题")

    assert result["answer"] == "直接答案"
    assert result["iterations"] == 1
    assert result["tool_calls"] == []
    assert result["degraded"] is False


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_llm_unavailable_degraded(mock_call):
    """LLM 不可用降级。"""
    mock_call.side_effect = LLMUnavailableError("所有 provider 不可用")
    agent = TestAgent()

    result = await agent.run("测试问题")

    assert result["degraded"] is True
    assert "服务暂时繁忙" in result["answer"]
    assert result["iterations"] == 0
    assert result["tool_calls"] == []


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_tool_failure_continues(mock_call):
    """工具失败降级：异常被捕获，observation 含错误信息，继续循环不中断。"""
    mock_call.side_effect = [
        'Action: search\nAction Input: {"query":"test"}',
        "Final Answer: 最终答案",
    ]
    agent = FailingToolAgent()

    result = await agent.run("测试问题")

    assert result["answer"] == "最终答案"
    assert result["degraded"] is False
    assert result["iterations"] == 2
    assert len(result["tool_calls"]) == 1
    assert "error" in result["tool_calls"][0]["result"]
    assert "工具执行失败" in result["tool_calls"][0]["result"]["error"]


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_think_returns_dict(mock_call):
    """think 节点返回 dict，不返回 None。"""
    mock_call.return_value = "Final Answer: 测试答案"
    agent = TestAgent()
    state: AgentState = {
        "messages": [HumanMessage(content="测试")],
        "iterations": 0,
        "final_answer": None,
        "tool_calls": [],
        "pending_action": None,
        "degraded": False,
    }

    result = await agent.think(state)

    assert isinstance(result, dict)
    assert "final_answer" in result
    assert result["final_answer"] == "测试答案"
    assert result["iterations"] == 1


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_act_returns_dict(mock_call):
    """act 节点返回 dict，不返回 None。"""
    agent = TestAgent()
    state: AgentState = {
        "messages": [HumanMessage(content="测试")],
        "iterations": 1,
        "final_answer": None,
        "tool_calls": [],
        "pending_action": {"name": "search", "input": {"query": "test"}},
        "degraded": False,
    }

    result = await agent.act(state)

    assert isinstance(result, dict)
    assert "tool_calls" in result
    assert len(result["tool_calls"]) == 1
    assert "messages" in result
