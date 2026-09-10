"""真实电商业务数据源：订单/物流/商品/售后数据落在 PostgreSQL。

返回形状与 MockMallDataSource 完全一致（见 app/core/mall/base.py 契约），
消费方（MCP Server 工具 / 门面）无需感知实现差异。

失败降级：PostgreSQL 查询失败 → logger.warning + 返回降级值（query_order/
query_product 返回 None、query_logistics 返回 []、apply_refund 返回失败 dict、
list_orders 返回 {"orders": [], "total": 0, "degraded": ["postgres"]}），
不抛异常中断 Agent 链路（与 mock 语义一致）。
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import func, select

from app.core.infra.postgres import async_session
from app.core.mall.base import MallDataSource
from app.models.mall import MallLogistics, MallOrder, MallOrderItem, MallProduct, MallRefund

logger = logging.getLogger(__name__)

# 可申请售后的订单状态（与 mock_source._REFUNDABLE_STATUSES 一致）
_REFUNDABLE_STATUSES = ("待发货", "已发货", "已完成")

# 契约里 created_at 的字符串格式（与 mock 数据一致，Agent 回复可直接引用）
_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def _to_amount(value: Decimal | float | int | None) -> float | int | None:
    """金额统一转数值（契约与 mock 一致：整数值返回 int，如 6999 而非 6999.0）。

    mock 数据源金额是 int，real 从 Numeric(12,2) 读出的 Decimal 必须归一化，
    保证两种实现 JSON 序列化后完全一致（6999 vs 6999.0 在 SSE payload 中不同）。
    NULL 值返回 None（与「未命中返回 None」的契约语义一致，不静默转 0）。
    """
    if value is None:
        return None
    num = float(value)
    return int(num) if num.is_integer() else num


class RealMallDataSource(MallDataSource):
    """PostgreSQL 实现的电商业务数据源。"""

    @property
    def source_mode(self) -> str:
        return "real"

    async def query_order(
        self, order_sn: str, *, requester_username: str, requester_role: str
    ) -> dict | None:
        """查询订单信息（订单 + 商品明细）。未知或无权访问时返回 None。"""
        try:
            async with async_session() as session:
                stmt = select(MallOrder).where(MallOrder.order_sn == order_sn)
                if requester_role not in {"agent", "admin"}:
                    stmt = stmt.where(MallOrder.owner_username == requester_username)
                order = (await session.execute(stmt)).scalar_one_or_none()
                if order is None:
                    return None
                result = await session.execute(
                    select(MallOrderItem).where(MallOrderItem.order_sn == order_sn)
                )
                items = [
                    {
                        "product_id": it.product_id,
                        "name": it.name,
                        "spec": it.spec,
                        "price": _to_amount(it.price),
                        "quantity": it.quantity,
                    }
                    for it in result.scalars().all()
                ]
                return {
                    "order_sn": order.order_sn,
                    "status": order.status,
                    "items": items,
                    "pay_amount": _to_amount(order.pay_amount),
                    "logistics_no": order.logistics_no,
                    "created_at": order.created_at.strftime(_DATETIME_FMT),
                }
        except Exception as e:
            logger.warning("[Mall] query_order 失败（PostgreSQL）: %s", e)
            return None

    async def list_orders(
        self,
        *,
        owner_username: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """查询订单列表（管理端）：过滤 + created_at 倒序 + 分页，total 为过滤后总数。

        PostgreSQL 失败 → logger.warning + 返回
        {"orders": [], "total": 0, "degraded": ["postgres"]}，不抛异常。
        """
        try:
            async with async_session() as session:
                stmt = select(MallOrder)
                if owner_username is not None:
                    stmt = stmt.where(MallOrder.owner_username == owner_username)
                if status is not None:
                    stmt = stmt.where(MallOrder.status == status)
                total = (
                    await session.execute(select(func.count()).select_from(stmt.subquery()))
                ).scalar_one()
                rows = (
                    await session.execute(
                        stmt.order_by(MallOrder.created_at.desc()).offset(offset).limit(limit)
                    )
                ).scalars().all()
                return {
                    "orders": [
                        {
                            "order_sn": row.order_sn,
                            "owner_username": row.owner_username,
                            "status": row.status,
                            "pay_amount": _to_amount(row.pay_amount),
                            "logistics_no": row.logistics_no,
                            "created_at": row.created_at.strftime(_DATETIME_FMT),
                        }
                        for row in rows
                    ],
                    "total": int(total),
                }
        except Exception as e:
            logger.warning("[Mall] list_orders 失败（PostgreSQL）: %s", e)
            return {"orders": [], "total": 0, "degraded": ["postgres"]}

    async def my_orders(
        self,
        *,
        requester_username: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """查询当前用户订单列表（含商品明细）：过滤 + created_at 倒序 + 分页。

        items 用 MallOrderItem 按 order_sn 关联查询组装（与 query_order 同模式，
        列表场景一次 in_ 查询批量取回）。total 为过滤后总数。

        PostgreSQL 失败 → logger.warning + 返回
        {"orders": [], "total": 0, "degraded": ["postgres"]}，不抛异常。
        """
        try:
            async with async_session() as session:
                stmt = select(MallOrder).where(MallOrder.owner_username == requester_username)
                if status is not None:
                    stmt = stmt.where(MallOrder.status == status)
                total = (
                    await session.execute(select(func.count()).select_from(stmt.subquery()))
                ).scalar_one()
                rows = (
                    await session.execute(
                        stmt.order_by(MallOrder.created_at.desc()).offset(offset).limit(limit)
                    )
                ).scalars().all()

                items_by_order: dict[str, list[dict]] = {}
                order_sns = [row.order_sn for row in rows]
                if order_sns:
                    item_result = await session.execute(
                        select(MallOrderItem)
                        .where(MallOrderItem.order_sn.in_(order_sns))
                        .order_by(MallOrderItem.id)
                    )
                    for it in item_result.scalars().all():
                        items_by_order.setdefault(it.order_sn, []).append(
                            {
                                "product_id": it.product_id,
                                "name": it.name,
                                "spec": it.spec,
                                "price": _to_amount(it.price),
                                "quantity": it.quantity,
                            }
                        )
                return {
                    "orders": [
                        {
                            "order_sn": row.order_sn,
                            "status": row.status,
                            "pay_amount": _to_amount(row.pay_amount),
                            "logistics_no": row.logistics_no,
                            "created_at": row.created_at.strftime(_DATETIME_FMT),
                            "items": items_by_order.get(row.order_sn, []),
                        }
                        for row in rows
                    ],
                    "total": int(total),
                }
        except Exception as e:
            logger.warning("[Mall] my_orders 失败（PostgreSQL）: %s", e)
            return {"orders": [], "total": 0, "degraded": ["postgres"]}

    async def query_logistics(
        self, order_sn: str, *, requester_username: str, requester_role: str
    ) -> list[dict]:
        """查询物流轨迹 [{ts, content}] 按时间正序。未发货/未知订单返回空列表。"""
        try:
            async with async_session() as session:
                order_stmt = select(MallOrder.order_sn).where(MallOrder.order_sn == order_sn)
                if requester_role not in {"agent", "admin"}:
                    order_stmt = order_stmt.where(MallOrder.owner_username == requester_username)
                if (await session.execute(order_stmt)).scalar_one_or_none() is None:
                    return []
                result = await session.execute(
                    select(MallLogistics)
                    .where(MallLogistics.order_sn == order_sn)
                    .order_by(MallLogistics.ts)
                )
                return [
                    {"ts": row.ts.strftime(_DATETIME_FMT), "content": row.content}
                    for row in result.scalars().all()
                ]
        except Exception as e:
            logger.warning("[Mall] query_logistics 失败（PostgreSQL）: %s", e)
            return []

    async def query_product(self, product_id: str) -> dict | None:
        """查询商品信息。未知 product_id 返回 None。"""
        try:
            async with async_session() as session:
                product = await session.get(MallProduct, product_id)
                if product is None:
                    return None
                return {
                    "id": product.id,
                    "name": product.name,
                    "spec": product.spec,
                    "price": _to_amount(product.price),
                    "stock": product.stock,
                    "services": list(product.services or []),
                }
        except Exception as e:
            logger.warning("[Mall] query_product 失败（PostgreSQL）: %s", e)
            return None

    async def apply_refund(
        self, order_sn: str, reason: str, *, requester_username: str, requester_role: str
    ) -> dict:
        """创建售后（退款）单。

        状态机与 mock 一致：
        - 未知订单 → 拒绝（订单不存在）
        - 待付款 → 拒绝（未支付不可售后）
        - 待发货 / 已发货 / 已完成 → 创建售后单，refund_id = AF{order_sn}
        - 重复申请 → 返回已存在的售后单（幂等，order_sn UNIQUE 约束兜底）
        """
        try:
            async with async_session() as session:
                stmt = select(MallOrder).where(MallOrder.order_sn == order_sn)
                if requester_role not in {"agent", "admin"}:
                    stmt = stmt.where(MallOrder.owner_username == requester_username)
                order = (await session.execute(stmt)).scalar_one_or_none()
                if order is None:
                    return {
                        "refund_id": None,
                        "status": "failed",
                        "message": "订单不存在或无权操作，无法申请退款",
                    }
                if order.status not in _REFUNDABLE_STATUSES:
                    # 文案与 mock 对齐：待付款给「先支付」提示；其他非可售后状态
                    # （如已取消/已拒收）动态带状态名，不硬编码「待付款」
                    if order.status == "待付款":
                        message = f"订单 {order_sn} 待付款，请先完成支付后再申请退款"
                    else:
                        message = f"订单 {order_sn} 当前状态（{order.status}）不支持退款"
                    return {
                        "refund_id": None,
                        "status": "failed",
                        "message": message,
                    }

                existing = await session.get(MallRefund, f"AF{order_sn}")
                if existing is not None:
                    return {
                        "refund_id": existing.refund_id,
                        "status": existing.status,
                        "message": (
                            f"订单 {order_sn} 已申请过售后（{existing.refund_id}），请勿重复提交"
                        ),
                    }

                refund = MallRefund(
                    refund_id=f"AF{order_sn}",
                    order_sn=order_sn,
                    reason=reason,
                    status="处理中",
                )
                session.add(refund)
                await session.commit()
                return {
                    "refund_id": refund.refund_id,
                    "status": refund.status,
                    "message": f"售后申请已提交，售后单号 {refund.refund_id}",
                }
        except Exception as e:
            logger.warning("[Mall] apply_refund 失败（PostgreSQL）: %s", e)
            return {
                "refund_id": None,
                "status": "failed",
                "message": f"售后申请暂时无法提交（{e}），请稍后重试或转人工客服",
            }

    async def list_refunds(
        self,
        *,
        status: str | None = None,
        owner_username: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        try:
            async with async_session() as session:
                stmt = select(MallRefund, MallOrder.owner_username, MallOrder.created_at).join(
                    MallOrder, MallOrder.order_sn == MallRefund.order_sn
                )
                if status is not None:
                    stmt = stmt.where(MallRefund.status == status)
                if owner_username is not None:
                    stmt = stmt.where(MallOrder.owner_username == owner_username)
                count_stmt = select(func.count()).select_from(stmt.subquery())
                total = (await session.execute(count_stmt)).scalar_one()
                rows = (
                    await session.execute(
                        stmt.order_by(MallRefund.created_at.desc()).offset(offset).limit(limit)
                    )
                ).all()
                return {
                    "refunds": [
                        {
                            "refund_id": refund.refund_id,
                            "order_sn": refund.order_sn,
                            "owner_username": owner,
                            "reason": refund.reason,
                            "status": refund.status,
                            "created_at": (refund.created_at or order_created).strftime(
                                _DATETIME_FMT
                            ),
                        }
                        for refund, owner, order_created in rows
                    ],
                    "total": int(total),
                }
        except Exception as e:
            logger.warning("[Mall] list_refunds 失败（PostgreSQL）: %s", e)
            return {"refunds": [], "total": 0, "degraded": ["postgres"]}

    async def update_refund_status(self, refund_id: str, new_status: str) -> dict:
        if new_status not in {"已通过", "已拒绝"}:
            raise ValueError(f"非法状态流转: 处理中 -> {new_status}")
        try:
            async with async_session() as session:
                refund = await session.get(MallRefund, refund_id)
                if refund is None:
                    raise LookupError(f"退款单不存在: {refund_id}")
                if refund.status != "处理中":
                    raise ValueError(f"非法状态流转: {refund.status} -> {new_status}")
                refund.status = new_status
                await session.commit()
                return {
                    "refund_id": refund.refund_id,
                    "order_sn": refund.order_sn,
                    "reason": refund.reason,
                    "status": refund.status,
                    "created_at": refund.created_at.isoformat() if refund.created_at else None,
                }
        except (LookupError, ValueError):
            raise
        except Exception as e:
            logger.warning("[Mall] update_refund_status 失败（PostgreSQL）: %s", e)
            raise RuntimeError("退款状态暂时无法更新") from e
