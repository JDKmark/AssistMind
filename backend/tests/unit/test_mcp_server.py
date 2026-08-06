"""MCP Server 单元测试。

覆盖 4 个工具函数：
1. search_knowledge 正常：返回 contexts 列表
2. search_knowledge 降级：retrieve 返回空 contexts，返回空列表
3. create_ticket 正常：返回 ticket_id/created/ticket
4. transfer_human：返回 message 含"转人工"和 ticket_id
5. get_ticket_status 存在：返回工单详情
6. get_ticket_status 不存在：返回 {error: "工单不存在"}

mock 策略：直接 patch server 模块中别名 import 的引用
（_retrieve / _create_ticket / _get_ticket），不连真实 RAG/DB/Redis。
工具函数被 @mcp.tool() 装饰后仍可直接 await 调用（装饰器返回原函数）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.core.mcp.server import (
    create_ticket,
    get_ticket_status,
    search_knowledge,
    transfer_human,
)


# ---------- 1. search_knowledge 正常 ----------


@patch("app.core.mcp.server._retrieve", new_callable=AsyncMock)
async def test_search_knowledge_normal(mock_retrieve):
    """search_knowledge 正常返回 contexts 列表。"""
    mock_retrieve.return_value = {
        "query": "AssistMind 是什么",
        "rewrites": {"degraded": False},
        "contexts": [
            {
                "doc_id": "doc1",
                "title": "简介",
                "source": "intro.md",
                "text": "AssistMind 是 SaaS 产品文档问答系统",
                "score": 0.9,
            },
            {
                "doc_id": "doc2",
                "title": "架构",
                "source": "arch.md",
                "text": "支持多路召回",
                "score": 0.7,
            },
        ],
        "crag": {"score": 0.9, "action": "generate", "degraded": False},
        "degraded": [],
    }

    result = await search_knowledge("AssistMind 是什么")

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["doc_id"] == "doc1"
    assert result[0]["title"] == "简介"
    assert "text" in result[0]
    mock_retrieve.assert_awaited_once_with("AssistMind 是什么", role="user")


# ---------- 2. search_knowledge 降级（空 contexts） ----------


@patch("app.core.mcp.server._retrieve", new_callable=AsyncMock)
async def test_search_knowledge_degraded_empty(mock_retrieve):
    """search_knowledge 降级：retrieve 返回空 contexts，应返回空列表。"""
    mock_retrieve.return_value = {
        "query": "不存在的问题",
        "rewrites": {"degraded": True},
        "contexts": [],
        "crag": {"score": 0.0, "action": "no_result", "degraded": False},
        "degraded": ["qdrant", "bm25"],
    }

    result = await search_knowledge("不存在的问题", role="agent")

    assert result == []
    mock_retrieve.assert_awaited_once_with("不存在的问题", role="agent")


# ---------- 3. create_ticket 正常 ----------


@patch("app.core.mcp.server._create_ticket", new_callable=AsyncMock)
async def test_create_ticket_normal(mock_create):
    """create_ticket 正常创建，返回 ticket_id/created/ticket。"""
    mock_create.return_value = {
        "ticket_id": "TK-1234567890abc",
        "created": True,
        "ticket": {
            "id": "TK-1234567890abc",
            "title": "登录失败",
            "description": "用户无法登录系统",
            "priority": "high",
            "status": "open",
            "category": "",
            "user_id": "system",
        },
    }

    result = await create_ticket("登录失败", "用户无法登录系统", priority="high")

    assert result["ticket_id"] == "TK-1234567890abc"
    assert result["created"] is True
    assert result["ticket"]["title"] == "登录失败"
    assert result["ticket"]["priority"] == "high"
    mock_create.assert_awaited_once_with(
        "登录失败", "用户无法登录系统", priority="high"
    )


# ---------- 4. transfer_human ----------


@patch("app.core.mcp.server._create_ticket", new_callable=AsyncMock)
async def test_transfer_human(mock_create):
    """transfer_human 返回 message 含"转人工"和 ticket_id。"""
    mock_create.return_value = {
        "ticket_id": "TK-transfer001",
        "created": True,
        "ticket": {
            "id": "TK-transfer001",
            "title": "转人工：用户要求人工服务",
            "description": "用户要求人工服务",
            "priority": "high",
            "status": "open",
            "category": "transfer_human",
            "user_id": "system",
        },
    }

    result = await transfer_human("用户要求人工服务")

    assert "人工" in result["message"]
    assert result["ticket_id"] == "TK-transfer001"
    # 验证内部以 high 优先级 + transfer_human 分类创建工单
    mock_create.assert_awaited_once_with(
        title="转人工：用户要求人工服务",
        description="用户要求人工服务",
        priority="high",
        category="transfer_human",
    )


# ---------- 5. get_ticket_status 存在 ----------


@patch("app.core.mcp.server._get_ticket", new_callable=AsyncMock)
async def test_get_ticket_status_exists(mock_get):
    """get_ticket_status 工单存在时返回工单详情。"""
    mock_get.return_value = {
        "id": "TK-status001",
        "title": "查询状态",
        "description": "测试工单",
        "priority": "normal",
        "status": "in_progress",
        "category": "",
        "user_id": "system",
        "created_at": "2026-08-04T10:00:00+00:00",
        "updated_at": "2026-08-04T11:00:00+00:00",
    }

    result = await get_ticket_status("TK-status001")

    assert result["id"] == "TK-status001"
    assert result["status"] == "in_progress"
    assert "error" not in result
    mock_get.assert_awaited_once_with("TK-status001")


# ---------- 6. get_ticket_status 不存在 ----------


@patch("app.core.mcp.server._get_ticket", new_callable=AsyncMock)
async def test_get_ticket_status_not_found(mock_get):
    """get_ticket_status 工单不存在时返回 {error: "工单不存在"}。"""
    mock_get.return_value = None

    result = await get_ticket_status("TK-notexist")

    assert result == {"error": "工单不存在"}
    mock_get.assert_awaited_once_with("TK-notexist")
