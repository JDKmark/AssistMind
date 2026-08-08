"""ToolAgent 单元测试。

覆盖 8 个场景：
1. task 意图触发工具：create_ticket 工具调用，最终答案含工单号
2. Retrieval Before Agency：先检索后决策
3. 参数缺失澄清：CLARIFY 标记，不调工具
4. MCP 不可用降级：连接失败返回降级话术
5. create_ticket 调用成功：验证参数传递与返回结构
6. 达迭代上限降级：Loop Breaker 触发
7. 多轮对话：history 注入上下文，Agent 基于上文订单号调用 query_logistics
8. 工具清单包含 4 个电商业务工具（query_order/query_logistics/query_product/apply_refund）

mock 策略：
- LLM：patch app.agents.base.call_llm（参考 test_base_agent.py）
- MCPClient：直接传入 MagicMock + AsyncMock 实例，避免真实连接
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.tool_agent import ToolAgent


def _make_mcp_mock(call_tool_return=None, connected=True):
    """构造 mock MCPClient。

    Args:
        call_tool_return: call_tool 的返回值（默认 {}）
        connected: is_connected 属性值（同时作为 connect() 返回值）
    """
    mcp = MagicMock()
    mcp.is_connected = connected
    mcp.connect = AsyncMock(return_value=connected)
    mcp.call_tool = AsyncMock(
        return_value=call_tool_return if call_tool_return is not None else {}
    )
    mcp.close = AsyncMock()
    return mcp


# ---------- 1. task 意图触发工具 ----------


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_task_intent_triggers_tool(mock_call):
    """task 意图触发工具：create_ticket 调用成功，最终答案含工单号。"""
    mcp = _make_mcp_mock(
        call_tool_return={
            "ticket_id": "TK-1234567",
            "created": True,
            "ticket": {"id": "TK-1234567", "title": "登录问题"},
        }
    )
    mock_call.side_effect = [
        'Action: create_ticket\nAction Input: {"title":"登录问题","description":"无法登录"}',
        "Final Answer: 已为您创建工单，工单号：TK-1234567，客服将尽快处理。",
    ]
    agent = ToolAgent(mcp_client=mcp)

    result = await agent.run("帮我创建一个工单，标题是登录问题，描述是无法登录")

    assert "TK-1234567" in result["answer"]
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "create_ticket"
    assert result["degraded"] is False


# ---------- 2. Retrieval Before Agency ----------


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_retrieval_before_agency(mock_call):
    """Retrieval Before Agency：非纯工单操作先检索，再决策。"""
    mcp = _make_mcp_mock(
        call_tool_return=[
            {"doc_id": "doc1", "title": "检索架构", "text": "支持向量+BM25", "score": 0.9}
        ]
    )
    # LLM 在检索后才被调用（第一轮 think 强制检索，不调 LLM）
    mock_call.return_value = "Final Answer: AssistMind 支持向量检索和 BM25 检索。"
    agent = ToolAgent(mcp_client=mcp)

    result = await agent.run("AssistMind 支持哪些检索方式？")

    # 验证先调用了 search_knowledge
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "search_knowledge"
    # 验证 LLM 在检索后才调用（仅 1 次）
    mock_call.assert_awaited_once()
    # 验证检索参数
    mcp.call_tool.assert_awaited_once()
    name, args = mcp.call_tool.call_args[0]
    assert name == "search_knowledge"
    assert "AssistMind" in args["query"]
    assert result["degraded"] is False


# ---------- 3. 参数缺失澄清 ----------


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_param_missing_clarify(mock_call):
    """参数缺失澄清：LLM 返回 CLARIFY 标记，不调用工具，返回追问话术。"""
    mcp = _make_mcp_mock(call_tool_return={})
    mock_call.return_value = "CLARIFY: 请提供工单的标题和详细描述，以便我为您创建工单。"
    agent = ToolAgent(mcp_client=mcp)

    result = await agent.run("帮我创建一个工单")

    assert "请提供" in result["answer"]
    assert result["tool_calls"] == []
    assert result["degraded"] is False
    # 确保没有调用任何工具
    mcp.call_tool.assert_not_awaited()


# ---------- 4. MCP 不可用降级 ----------


async def test_mcp_unavailable_degraded():
    """MCP 不可用降级：连接失败返回降级话术，degraded=True。"""
    mcp = _make_mcp_mock(connected=False)

    agent = ToolAgent(mcp_client=mcp)

    result = await agent.run("测试问题")

    assert result["degraded"] is True
    assert "不可用" in result["answer"]
    assert result["tool_calls"] == []
    assert result["iterations"] == 0
    mcp.connect.assert_awaited_once()
    mcp.call_tool.assert_not_awaited()


# ---------- 5. create_ticket 调用成功 ----------


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_create_ticket_success(mock_call):
    """create_ticket 调用成功：验证工具参数正确传递、返回结构正确。"""
    ticket_result = {
        "ticket_id": "TK-9876543",
        "created": True,
        "ticket": {
            "id": "TK-9876543",
            "title": "测试标题",
            "description": "测试描述",
            "priority": "high",
            "status": "open",
        },
    }
    mcp = _make_mcp_mock(call_tool_return=ticket_result)
    mock_call.side_effect = [
        'Action: create_ticket\nAction Input: {"title":"测试标题","description":"测试描述","priority":"high"}',
        "Final Answer: 工单已创建，编号 TK-9876543。",
    ]
    agent = ToolAgent(mcp_client=mcp)

    result = await agent.run("帮我创建一个工单，标题是测试标题，描述是测试描述")

    # 验证工具参数正确传递
    mcp.call_tool.assert_awaited_once()
    name, args = mcp.call_tool.call_args[0]
    assert name == "create_ticket"
    assert args["title"] == "测试标题"
    assert args["description"] == "测试描述"
    assert args["priority"] == "high"

    # 验证返回结构
    assert result["tool_calls"][0]["result"] == ticket_result
    assert result["tool_calls"][0]["result"]["ticket_id"] == "TK-9876543"
    assert result["tool_calls"][0]["result"]["ticket"]["status"] == "open"
    assert "TK-9876543" in result["answer"]


# ---------- 6. 达迭代上限降级 ----------


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_max_iterations_degraded(mock_call):
    """达迭代上限降级：LLM 持续不返回 final_answer，Loop Breaker 触发。"""
    mcp = _make_mcp_mock(
        call_tool_return={"ticket_id": "TK-LOOP", "created": True, "ticket": {}}
    )
    # LLM 始终返回工具调用，不给最终答案
    mock_call.return_value = (
        'Action: create_ticket\nAction Input: {"title":"测试","description":"测试"}'
    )
    agent = ToolAgent(mcp_client=mcp)
    assert agent.max_iterations == 5

    result = await agent.run("帮我创建一个工单，标题是测试，描述是测试")

    assert result["iterations"] == 5
    assert result["degraded"] is True
    assert "无法处理" in result["answer"]


# ---------- 7. 多轮对话：history 注入上下文 ----------


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_multiturn_history_injects_context(mock_call):
    """多轮对话：run(history=...) 把上文注入 messages，Agent 能基于上文订单号调工具。

    场景：第一轮查订单，第二轮「物流到哪了」依赖上文订单号 20240801001。
    Retrieval Before Agency 使第一轮 think 强制 search_knowledge（不调 LLM），
    第二轮 LLM 读取上文后应调用 query_logistics(order_sn=20240801001)。
    """
    mcp = _make_mcp_mock(
        call_tool_return=[
            {"ts": "2024-08-01 16:00:00", "content": "已揽收"},
            {"ts": "2024-08-01 18:30:00", "content": "运输中（预计明天送达）"},
        ]
    )
    # 第一轮 think 被 Retrieval Before Agency 接管（不调 LLM），
    # 之后 LLM 先返回工具调用，再返回最终答案
    mock_call.side_effect = [
        'Action: query_logistics\nAction Input: {"order_sn":"20240801001"}',
        "Final Answer: 您的订单 20240801001 物流轨迹：已揽收 → 运输中，预计明天送达。",
    ]
    agent = ToolAgent(mcp_client=mcp)

    result = await agent.run(
        "物流到哪了？",
        history=[
            {"role": "user", "content": "查一下订单 20240801001"},
            {"role": "assistant", "content": "您的订单 20240801001 已发货。"},
        ],
    )

    # 最终工具链包含 query_logistics，且订单号来自上文
    assert any(tc["name"] == "query_logistics" for tc in result["tool_calls"])
    last_name, last_args = mcp.call_tool.await_args_list[-1].args
    assert last_name == "query_logistics"
    assert last_args["order_sn"] == "20240801001"
    assert result["degraded"] is False

    # 历史上下文确实被注入 LLM 提示（prompt 含上文用户/客服消息与当前问题）
    prompt = mock_call.await_args.args[0]
    assert "查一下订单 20240801001" in prompt
    assert "您的订单 20240801001 已发货" in prompt
    assert "物流到哪了" in prompt


# ---------- 8. 工具清单包含电商业务工具 ----------


def test_get_tools_includes_mall_tools():
    """get_tools() 必须暴露 4 个电商业务工具（评估脚本依赖 query_order 等）。"""
    agent = ToolAgent(mcp_client=_make_mcp_mock())
    names = {t["name"] for t in agent.get_tools()}

    assert {"query_order", "query_logistics", "query_product", "apply_refund"} <= names
    assert "search_knowledge" in names


# ---------- 9. 实体识别参数补填 ----------


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_entity_fill_missing_order_sn(mock_call):
    """LLM 决策 query_order 但 input 缺 order_sn：实体补填后调用。

    场景：用户问题含订单号 20240801001，LLM 输出 Action 但参数为空。
    Retrieval Before Agency 首轮检索 → 实体提示注入（空转一轮）→ LLM 决策 → 补填调用。
    """
    mcp = _make_mcp_mock(call_tool_return={"order_sn": "20240801001", "status": "已发货"})
    mock_call.side_effect = [
        'Action: query_order\nAction Input: {}',
        "Final Answer: 您的订单 20240801001 已发货。",
    ]
    agent = ToolAgent(mcp_client=mcp)

    result = await agent.run("查一下订单 20240801001 到哪了")

    # 工具调用参数被实体补填
    order_calls = [
        call for call in mcp.call_tool.await_args_list if call.args[0] == "query_order"
    ]
    assert len(order_calls) == 1
    assert order_calls[0].args[1]["order_sn"] == "20240801001"
    assert "已发货" in result["answer"]
    assert result["degraded"] is False


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_entity_fill_product_id(mock_call):
    """LLM 决策 query_product 但 input 缺 product_id：实体补填。"""
    mcp = _make_mcp_mock(call_tool_return={"id": "P001", "name": "华为 Mate 60 Pro"})
    mock_call.side_effect = [
        'Action: query_product\nAction Input: {}',
        "Final Answer: 华为 Mate 60 Pro 当前在售。",
    ]
    agent = ToolAgent(mcp_client=mcp)

    result = await agent.run("P001 这个商品还有货吗")

    product_calls = [
        call for call in mcp.call_tool.await_args_list if call.args[0] == "query_product"
    ]
    assert len(product_calls) == 1
    assert product_calls[0].args[1]["product_id"] == "P001"


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_no_entity_no_fill_keeps_original(mock_call):
    """无实体时参数原样传递（create_ticket 不受影响）。"""
    ticket_result = {"ticket_id": "TK-1234567", "created": True, "ticket": {}}
    mcp = _make_mcp_mock(call_tool_return=ticket_result)
    mock_call.side_effect = [
        'Action: create_ticket\nAction Input: {"title":"标题","description":"描述"}',
        "Final Answer: 已创建工单 TK-1234567。",
    ]
    agent = ToolAgent(mcp_client=mcp)

    result = await agent.run("帮我创建一个工单，标题是标题，描述是描述")

    name, args = mcp.call_tool.await_args_list[-1].args
    assert name == "create_ticket"
    assert args == {"title": "标题", "description": "描述"}
    assert "TK-1234567" in result["answer"]


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_multiturn_entity_from_history_fills_args(mock_call):
    """多轮「物流到哪了」：当前问题无实体，从历史提取订单号并补填参数。

    Retrieval Before Agency 首轮检索 → 实体提示注入（空转）→ LLM 决策
    但 input 缺 order_sn → 从历史实体补填。
    """
    mcp = _make_mcp_mock(
        call_tool_return=[
            {"ts": "2024-08-01 16:00:00", "content": "已揽收"},
            {"ts": "2024-08-01 18:30:00", "content": "运输中"},
        ]
    )
    mock_call.side_effect = [
        'Action: query_logistics\nAction Input: {}',
        "Final Answer: 物流轨迹：已揽收 → 运输中。",
    ]
    agent = ToolAgent(mcp_client=mcp)

    result = await agent.run(
        "物流到哪了？",
        history=[
            {"role": "user", "content": "查一下订单 20240801001"},
            {"role": "assistant", "content": "您的订单 20240801001 已发货。"},
        ],
    )

    logistics_calls = [
        call
        for call in mcp.call_tool.await_args_list
        if call.args[0] == "query_logistics"
    ]
    assert len(logistics_calls) == 1
    assert logistics_calls[0].args[1]["order_sn"] == "20240801001"
    assert result["degraded"] is False


# ---------- 10. 缺失槽位提示注入 ----------


@patch("app.agents.base.call_llm", new_callable=AsyncMock)
async def test_think_injects_missing_slot_hint(mock_call):
    """用户已提供订单号但缺退款原因：think 注入缺失槽位提示，不重复索要订单号。

    场景：query 含订单号（实体提示注入分支触发）+ 退货意图缺 reason →
    注入「还缺reason」提示；LLM mock 直接返回 Final Answer，整体 run 正常结束。
    """
    mcp = _make_mcp_mock(call_tool_return={"order_sn": "20240801001", "status": "已发货"})
    mock_call.return_value = "Final Answer: 好的，已收到订单号 20240801001，请问退款原因是？"
    agent = ToolAgent(mcp_client=mcp)

    result = await agent.run("订单号是 20240801001，我要退货")

    # LLM 提示（已有对话）中注入了缺失槽位提示：还缺 reason
    prompt = mock_call.await_args.args[0]
    assert "还缺reason" in prompt
    # 不重复索要已有槽位：订单号不在缺失清单中
    assert "还缺order_sn" not in prompt
    assert "订单号 20240801001" in prompt
    assert result["degraded"] is False

