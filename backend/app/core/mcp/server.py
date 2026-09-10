"""MCP Server：向 Agent 暴露知识库检索、运维数据与工单工具。

使用 mcp 2.0.0 的 MCPServer（mcp.server.mcpserver.MCPServer），
通过 streamable_http 传输挂载到 FastAPI。

工具清单（13 个）：
- search_knowledge：知识库检索（运维故障案例手册）
- query_metric：查询服务指标时序
- search_log：搜索服务日志
- query_change：查询变更记录（部署/配置）
- get_alerts：查询告警
- create_incident：创建故障工单（severity 映射 priority）
- create_ticket / transfer_human / get_ticket_status：客服工单（保留）
- query_order / query_logistics / query_product / apply_refund：电商业务（mall 门面）

注意：工具函数名 create_ticket 与 ticket_service.create_ticket 重名，
内部调用用别名 import（_create_ticket / _get_ticket / _retrieve）。
"""

from __future__ import annotations

import logging

from mcp.server.mcpserver import Context, MCPServer

from app.core.mall import data_source as mall_ds
from app.core.ops import data_source as ops_ds
from app.core.rag.engine import retrieve as _retrieve
from app.core.security.auth import decode_access_token
from app.core.ticket_service import create_ticket as _create_ticket
from app.core.ticket_service import get_ticket as _get_ticket

logger = logging.getLogger(__name__)

mcp = MCPServer("AssistOps")


def _requester(ctx: Context) -> tuple[str, str]:
    headers = ctx.headers or {}
    authorization = headers.get("authorization") or headers.get("Authorization") or ""
    if not authorization.startswith("Bearer "):
        return "", ""
    try:
        payload = decode_access_token(authorization.removeprefix("Bearer ").strip())
    except Exception:
        return "", ""
    return str(payload.get("sub") or ""), str(payload.get("role") or "user")


@mcp.tool()
async def search_knowledge(query: str, role: str = "user") -> list[dict]:
    """搜索运维知识库（故障案例手册），返回相关文档片段。

    Args:
        query: 用户查询问题（如"数据库连接池耗尽如何排查"）
        role: 用户角色（user/agent/admin），用于 RBAC 过滤

    Returns:
        相关文档列表，每个含 doc_id/title/source/text/score
    """
    result = await _retrieve(query, role=role)
    return result["contexts"]


@mcp.tool()
async def query_metric(
    service: str,
    metric: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> dict:
    """查询服务监控指标时序数据。

    Args:
        service: 服务名（api-gateway/order-service/inventory-service/payment-service/user-service）
        metric: 指标名（cpu_usage/memory_usage/error_rate/latency_p95/qps）
        start_ts: 起始时间（epoch 秒，默认 2 小时前）
        end_ts: 结束时间（epoch 秒，默认当前）

    Returns:
        {service, metric, points: [{ts, value}], summary: {current, max, min, avg}}
    """
    points = await ops_ds.query_metric(service, metric, start_ts, end_ts)
    if not points:
        return {"service": service, "metric": metric, "points": [], "summary": {}}
    values = [p["value"] for p in points]
    return {
        "service": service,
        "metric": metric,
        "points": points,
        "summary": {
            "current": values[-1],
            "max": round(max(values), 2),
            "min": round(min(values), 2),
            "avg": round(sum(values) / len(values), 2),
        },
    }


@mcp.tool()
async def search_log(
    service: str | None = None,
    keyword: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """搜索服务日志。

    Args:
        service: 服务名（可选，不过滤则搜索全部）
        keyword: 关键字（可选，如"connection pool"/"slow query"）
        start_ts: 起始时间（epoch 秒）
        end_ts: 结束时间（epoch 秒）
        limit: 返回条数上限

    Returns:
        日志列表 [{ts, service, level, message, trace_id}]
    """
    return await ops_ds.search_logs(
        service=service,
        keyword=keyword,
        start_ts=start_ts,
        end_ts=end_ts,
        limit=limit,
    )


@mcp.tool()
async def query_change(
    service: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int = 10,
) -> list[dict]:
    """查询变更记录（部署/配置/扩容）。

    Args:
        service: 服务名（可选）
        start_ts: 起始时间（epoch 秒）
        end_ts: 结束时间（epoch 秒）

    Returns:
        变更列表 [{ts, service, type, content}]
    """
    return await ops_ds.query_changes(
        service=service, start_ts=start_ts, end_ts=end_ts, limit=limit
    )


@mcp.tool()
async def get_alerts(service: str | None = None) -> list[dict]:
    """查询当前告警列表。

    Args:
        service: 服务名（可选）

    Returns:
        告警列表 [{alert_id, service, metric, severity, ts, message}]
    """
    return await ops_ds.get_alerts(service=service)


@mcp.tool()
async def create_incident(
    title: str, description: str, severity: str = "medium", ctx: Context = None
) -> dict:
    """创建故障工单（incident）。

    Args:
        title: 故障标题（必填）
        description: 故障描述与诊断结论（必填）
        severity: 严重级别 low/medium/high/critical，映射到工单 priority

    Returns:
        {ticket_id, created, ticket}
    """
    priority_map = {"low": "low", "medium": "normal", "high": "high", "critical": "urgent"}
    priority = priority_map.get(severity, "normal")
    username, _ = _requester(ctx) if ctx is not None else ("", "")
    return await _create_ticket(
        title, description, priority=priority, category="incident",
        user_id=username or "system",
    )


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
    username, _ = _requester(ctx) if ctx is not None else ("", "")
    return await _create_ticket(
        title, description, priority=priority, user_id=username or "system"
    )


@mcp.tool()
async def transfer_human(reason: str, ctx: Context = None) -> dict:
    """转人工客服。创建一个标记为转人工的工单并返回提示话术。

    Args:
        reason: 转人工原因

    Returns:
        {message, ticket_id}
    """
    username, _ = _requester(ctx) if ctx is not None else ("", "")
    result = await _create_ticket(
        title=f"转人工：{reason[:50]}",
        description=reason,
        priority="high",
        category="transfer_human",
        user_id=username or "system",
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
    username, role = _requester(ctx) if ctx is not None else ("", "")
    ticket = await _get_ticket(ticket_id)
    if ticket is None:
        return {"error": "工单不存在"}
    # user 角色归属隔离：他人工单与不存在统一形状（防枚举）
    if role == "user" and ticket.get("user_id") != username:
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
    username, role = _requester(ctx)
    order = await mall_ds.query_order(order_sn, requester_username=username, requester_role=role)
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
    username, role = _requester(ctx)
    return await mall_ds.query_logistics(order_sn, requester_username=username, requester_role=role)


@mcp.tool()
async def query_product(product_id: str) -> dict:
    """查询商品信息（价格/库存/服务标识）。

    Args:
        product_id: 商品 ID（如 P001）

    Returns:
        商品信息 {id, name, spec, price, stock, services}；
        商品不存在返回 {"error": "商品不存在"}
    """
    product = await mall_ds.query_product(product_id)
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
    username, role = _requester(ctx)
    return await mall_ds.apply_refund(
        order_sn, reason, requester_username=username, requester_role=role
    )


# 模块级缓存：streamable_http_app() 只能调用一次（内部创建 session_manager）
_mcp_app = None
_mcp_session_manager = None


def get_mcp_app():
    """获取 MCP Server 的 ASGI app，用于挂载到 FastAPI。

    返回 Starlette 实例（ASGI）。streamable_http_path 设为 '/'，
    配合 FastAPI app.mount('/mcp', ...) 使 MCP 端点位于 /mcp/。

    注意：streamable_http_app() 内部创建 StreamableHTTPSessionManager 并存到
    mcp._lowlevel_server._session_manager。由于挂载到 FastAPI 时子 app 的
    lifespan 不会被调用，必须在 FastAPI lifespan 中手动调用
    get_mcp_session_manager().run() 来初始化 task group，否则请求会抛
    "Task group is not initialized"。
    """
    global _mcp_app, _mcp_session_manager
    if _mcp_app is None:
        _mcp_app = mcp.streamable_http_app(streamable_http_path="/")
        _mcp_session_manager = mcp._lowlevel_server._session_manager
    return _mcp_app


def get_mcp_session_manager():
    """获取 MCP session manager，供 FastAPI lifespan 调用 run()。

    必须先调用 get_mcp_app() 触发 session manager 创建。
    """
    if _mcp_session_manager is None:
        get_mcp_app()
    return _mcp_session_manager
