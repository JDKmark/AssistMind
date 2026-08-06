"""MCP Client 单元测试。

覆盖 6 个场景：
1. 正常调用 call_tool：mock _session.call_tool 返回成功，验证解析结果
2. 连接失败降级：mock connect 返回 False，验证返回 {"error": "MCP Server 不可用"}
3. 超时降级：mock _session.call_tool 抛 TimeoutError，验证返回 {"error": "...调用失败..."}
4. list_tools 正常：mock _session.list_tools 返回工具列表
5. list_tools 失败：mock _session.list_tools 抛异常，返回空列表
6. close：验证关闭后 is_connected=False

mock 策略：直接在 MCPClient 实例上设置 _connected=True 和 _session=AsyncMock，
绕过真实 MCP 协议连接；connect 失败场景 patch connect 方法。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

from app.core.mcp.client import MCPClient


# ---------- 辅助：构造 mock CallToolResult ----------


def _make_tool_result(structured_content=None, content_texts=None):
    """构造 mock CallToolResult。"""
    result = Mock()
    result.structured_content = structured_content
    result.is_error = False
    if content_texts is not None:
        result.content = [Mock(text=t) for t in content_texts]
    else:
        result.content = []
    return result


def _make_list_tools_result(tools):
    """构造 mock ListToolsResult。"""
    result = Mock()
    tool_mocks = []
    for n, d in tools:
        t = Mock()
        t.name = n
        t.description = d
        tool_mocks.append(t)
    result.tools = tool_mocks
    return result


# ---------- 1. 正常调用 call_tool ----------


async def test_call_tool_normal():
    """call_tool 正常调用，返回 structured_content 中的数据。"""
    client = MCPClient()
    client._connected = True
    client._session = AsyncMock()
    expected = [
        {"doc_id": "doc1", "title": "简介", "text": "AssistMind 是问答系统", "score": 0.9},
    ]
    client._session.call_tool.return_value = _make_tool_result(
        structured_content=expected
    )

    result = await client.call_tool("search_knowledge", {"query": "AssistMind 是什么"})

    assert result == expected
    assert result[0]["doc_id"] == "doc1"
    client._session.call_tool.assert_awaited_once_with(
        "search_knowledge", {"query": "AssistMind 是什么"}
    )


# ---------- 2. 连接失败降级 ----------


async def test_call_tool_connect_fail():
    """连接失败时 call_tool 返回 {"error": "MCP Server 不可用"}。"""
    client = MCPClient()
    client._connected = False

    with patch.object(client, "connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = False
        result = await client.call_tool("search_knowledge", {"query": "test"})

    assert result == {"error": "MCP Server 不可用"}
    mock_connect.assert_awaited_once()


# ---------- 3. 超时降级 ----------


async def test_call_tool_timeout():
    """call_tool 超时（抛 TimeoutError），返回 {"error": "...调用失败..."}。"""
    client = MCPClient()
    client._connected = True
    client._session = AsyncMock()
    client._session.call_tool.side_effect = TimeoutError("连接超时")

    result = await client.call_tool("create_ticket", {"title": "测试"})

    assert isinstance(result, dict)
    assert "error" in result
    assert "create_ticket" in result["error"]
    assert "调用失败" in result["error"]
    assert client.is_connected is False


# ---------- 4. list_tools 正常 ----------


async def test_list_tools_normal():
    """list_tools 正常返回工具列表。"""
    client = MCPClient()
    client._connected = True
    client._session = AsyncMock()
    client._session.list_tools.return_value = _make_list_tools_result([
        ("search_knowledge", "搜索知识库"),
        ("create_ticket", "创建工单"),
        ("transfer_human", "转人工"),
        ("get_ticket_status", "查询工单状态"),
    ])

    tools = await client.list_tools()

    assert len(tools) == 4
    assert tools[0]["name"] == "search_knowledge"
    assert tools[0]["description"] == "搜索知识库"
    assert tools[1]["name"] == "create_ticket"
    assert tools[3]["name"] == "get_ticket_status"
    client._session.list_tools.assert_awaited_once()


# ---------- 5. list_tools 失败 ----------


async def test_list_tools_fail():
    """list_tools 异常时返回空列表。"""
    client = MCPClient()
    client._connected = True
    client._session = AsyncMock()
    client._session.list_tools.side_effect = RuntimeError("连接断开")

    tools = await client.list_tools()

    assert tools == []


# ---------- 6. close ----------


async def test_close():
    """close 后 is_connected 为 False，_session 为 None。"""
    client = MCPClient()
    client._connected = True
    client._session = AsyncMock()

    await client.close()

    assert client.is_connected is False
    assert client._session is None
