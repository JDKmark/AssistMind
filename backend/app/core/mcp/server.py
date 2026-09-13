"""MCP Server：向 Agent 暴露知识库检索、客服工单与电商业务工具。

使用 mcp 2.0.0 的 MCPServer（mcp.server.mcpserver.MCPServer），
通过 streamable_http 传输挂载到 FastAPI。

工具清单（8 个）：
- search_knowledge：知识库检索
- create_ticket / transfer_human / get_ticket_status：客服工单
- query_order / query_logistics / query_product / apply_refund：电商业务（mall 门面）

注意：工具函数名 create_ticket 与 ticket_service.create_ticket 重名，
内部调用用别名 import（_create_ticket / _get_ticket / _retrieve）。
"""

from __future__ import annotations

import logging

from mcp.server.mcpserver import Context, MCPServer

from app.api.deps import resolve_token_identity
from app.config import get_settings
from app.core.infra.redis import get_redis
from app.core.mall import data_source as mall_ds
from app.core.mcp.security import MCPAuthMiddleware
from app.core.rag.engine import retrieve as _retrieve
from app.core.ticket_service import create_ticket as _create_ticket
from app.core.ticket_service import get_ticket as _get_ticket

logger = logging.getLogger(__name__)
settings = get_settings()

mcp = MCPServer("AssistMind-MCP")


async def _requester(ctx: Context | None) -> dict:
    headers = (ctx.headers or {}) if ctx is not None else {}
    authorization = headers.get("authorization") or headers.get("Authorization") or ""
    if not authorization.startswith("Bearer "):
        raise ValueError("未认证")
    return await resolve_token_identity(authorization.removeprefix("Bearer ").strip())


@mcp.tool()
async def search_knowledge(query: str, ctx: Context = None) -> list[dict]:
    """搜索知识库，返回相关文档片段。

    Args:
        query: 用户查询问题

    Returns:
        相关文档列表，每个含 doc_id/title/source/text/score

    role 只取自身份上下文（token），不接受调用方传入，防角色提权绕过知识库 RBAC。
    """
    identity = await _requester(ctx)
    result = await _retrieve(query, role=identity["role"])
    return result["contexts"]


@mcp.tool()
async def create_ticket(
    title: str, description: str, priority: str = "normal", ctx: Context = None
) -> dict:
    """创建客服工单。

    Args:
        title: 工单标题（必填）
        description: 问题描述（必填）
        priority: 优先级 low/normal/high/urgent，默认 normal

    Returns:
        {ticket_id, created, ticket}
    """
    identity = await _requester(ctx)
    return await _create_ticket(
        title, description, priority=priority, user_id=identity["username"]
    )


@mcp.tool()
async def transfer_human(reason: str, ctx: Context = None) -> dict:
    """转人工客服。创建一个标记为转人工的工单并返回提示话术。

    Args:
        reason: 转人工原因

    Returns:
        {message, ticket_id}
    """
    identity = await _requester(ctx)
    result = await _create_ticket(
        title=f"转人工：{reason[:50]}",
        description=reason,
        priority="high",
        category="transfer_human",
        user_id=identity["username"],
    )
    return {
        "message": "已为您转接人工客服，客服人员将尽快与您联系。工单号：" + result["ticket_id"],
        "ticket_id": result["ticket_id"],
    }


@mcp.tool()
async def get_ticket_status(ticket_id: str, ctx: Context = None) -> dict:
    """查询工单状态。

    Args:
        ticket_id: 工单 ID（TK- 开头）

    Returns:
        工单详情；工单不存在或无权访问返回 {"error": "工单不存在"}
    """
    identity = await _requester(ctx)
    ticket = await _get_ticket(ticket_id)
    if ticket is None:
        return {"error": "工单不存在"}
    # user 角色归属隔离：他人工单与不存在统一形状（防枚举）
    if identity["role"] == "user" and ticket.get("user_id") != identity["username"]:
        return {"error": "工单不存在"}
    return ticket


@mcp.tool()
async def query_order(order_sn: str, ctx: Context) -> dict:
    """查询电商订单信息（状态/商品明细/实付金额/物流单号/下单时间）。

    Args:
        order_sn: 订单号（如 20240801001）

    Returns:
        订单详情 {order_sn, status, items: [{product_id, name, spec, price, quantity}],
        pay_amount, logistics_no, created_at}；订单不存在返回 {"error": "订单不存在"}
    """
    identity = await _requester(ctx)
    order = await mall_ds.query_order(
        order_sn,
        requester_user_id=identity.get("user_id") or "",
        requester_username=identity["username"],
        requester_role=identity["role"],
    )
    if order is None:
        return {"error": "订单不存在"}
    return order


@mcp.tool()
async def query_logistics(order_sn: str, ctx: Context) -> list[dict]:
    """查询订单物流轨迹（按时间正序）。

    Args:
        order_sn: 订单号（如 20240801001）

    Returns:
        物流轨迹列表 [{ts, content}]；未发货或订单不存在返回空列表 []
    """
    identity = await _requester(ctx)
    return await mall_ds.query_logistics(
        order_sn,
        requester_user_id=identity.get("user_id") or "",
        requester_username=identity["username"],
        requester_role=identity["role"],
    )


@mcp.tool()
async def query_product(product_id: str, ctx: Context = None) -> dict:
    """查询商品信息（价格/服务标识/库存状态；精确库存仅管理员可见）。

    Args:
        product_id: 商品 ID（如 P001）

    Returns:
        商品信息 {id, name, spec, price, stock_status, services}；admin 额外含 stock；
        商品不存在返回 {"error": "商品不存在"}
    """
    identity = await _requester(ctx)
    product = await mall_ds.query_product(product_id, requester_role=identity["role"])
    if product is None:
        return {"error": "商品不存在"}
    return product


@mcp.tool()
async def apply_refund(order_sn: str, reason: str, ctx: Context) -> dict:
    """申请订单退款（创建售后单）。校验逻辑由数据源处理。

    Args:
        order_sn: 订单号（如 20240801001）
        reason: 退款原因（如"七天无理由退货"/"商品质量问题"）

    Returns:
        {refund_id, status, message}：
        - 成功：refund_id=AF{order_sn}，status=处理中
        - 待付款/未知订单拒绝：refund_id=None，status=failed（message 含原因）
        - 重复申请幂等：返回已存在的售后单
    """
    identity = await _requester(ctx)
    return await mall_ds.apply_refund(
        order_sn,
        reason,
        requester_user_id=identity.get("user_id") or "",
        requester_username=identity["username"],
        requester_role=identity["role"],
    )


# 模块级缓存：streamable_http_app() 只能调用一次（内部创建 session_manager）
_mcp_app = None
_mcp_session_manager = None


def get_mcp_app():
    """获取 MCP Server 的 ASGI app，用于挂载到 FastAPI。

    返回外层包了 MCPAuthMiddleware 的 ASGI 实例（统一 Bearer 认证 + 身份维度限流），
    streamable_http_path 设为 '/'，配合 FastAPI app.mount('/mcp', ...) 使 MCP 端点位于 /mcp/。

    注意：streamable_http_app() 内部创建 StreamableHTTPSessionManager 并存到
    mcp._lowlevel_server._session_manager。由于挂载到 FastAPI 时子 app 的
    lifespan 不会被调用，必须在 FastAPI lifespan 中手动调用
    get_mcp_session_manager().run() 来初始化 task group，否则请求会抛
    "Task group is not initialized"。
    """
    global _mcp_app, _mcp_session_manager
    if _mcp_app is None:
        # json_response=True：POST 响应走普通 JSON。本机（Windows/CPU 慢工具）实测
        # SSE 响应流模式下的长耗时工具调用（search_knowledge ~5-20s）会被提前掐断
        # （客户端报 "SSE stream ended without a response"）。JSON 模式下同任务复用
        # 与跨任务一次性调用（_one_shot_call）均实测稳定；会话保持有状态（GET 流
        # 承载 202 异步响应，stateless 会令其失效）。工具均为请求-响应模式，无服务
        # 端推送需求，不影响 MCP 协议语义。
        _mcp_app = mcp.streamable_http_app(streamable_http_path="/", json_response=True)
        _mcp_session_manager = mcp._lowlevel_server._session_manager
        _mcp_app = MCPAuthMiddleware(
            _mcp_app,
            redis=get_redis(),
            limit=settings.RATE_LIMIT_PER_MINUTE,
            period=60,
            key_prefix="scqa:rl:mcp",
        )
    return _mcp_app


def get_mcp_session_manager():
    """获取 MCP session manager，供 FastAPI lifespan 调用 run()。

    必须先调用 get_mcp_app() 触发 session manager 创建。
    """
    if _mcp_session_manager is None:
        get_mcp_app()
    return _mcp_session_manager
