"""真实电商业务数据源：订单/物流/商品/售后数据落在 PostgreSQL。

返回形状与 MockMallDataSource 完全一致（见 app/core/mall/base.py 契约），
消费方（MCP Server 工具 / 门面）无需感知实现差异。

失败降级：PostgreSQL 查询失败 → logger.warning + 返回降级值（query_order/
query_product 返回 None、query_logistics 返回 []、apply_refund 返回失败 dict），
不抛异常中断 Agent 链路（与 mock 语义一致）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core.infra.postgres import async_session
from app.core.mall.base import MallDataSource
from app.models.mall import MallLogistics, MallOrder, MallOrderItem, MallProduct, MallRefund

logger = logging.getLogger(__name__)

# 可申请售后的订单状态（与 mock_source._REFUNDABLE_STATUSES 一致）
_REFUNDABLE_STATUSES = ("待发货", "已发货", "已完成")

# 契约里 created_at 的字符串格式（与 mock 数据一致，Agent 回复可直接引用）
_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def _to_amount(value: Decimal | float | int | None) -> float | int:
    """金额统一转数值（契约与 mock 一致：整数值返回 int，如 6999 而非 6999.0）。

    mock 数据源金额是 int，real 从 Numeric(12,2) 读出的 Decimal 必须归一化，
    保证两种实现 JSON 序列化后完全一致（6999 vs 6999.0 在 SSE payload 中不同）。
    """
    if value is None:
        return 0
    num = float(value)
    return int(num) if num.is_integer() else num


class RealMallDataSource(MallDataSource):
    """PostgreSQL 实现的电商业务数据源。"""

    @property
    def source_mode(self) -> str:
        return "real"

    async def query_order(self, order_sn: str) -> dict | None:
        """查询订单信息（订单 + 商品明细）。未知 order_sn 返回 None。"""
        try:
            async with async_session() as session:
                order = await session.get(MallOrder, order_sn)
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

    async def query_logistics(self, order_sn: str) -> list[dict]:
        """查询物流轨迹 [{ts, content}] 按时间正序。未发货/未知订单返回空列表。"""
        try:
            async with async_session() as session:
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

    async def apply_refund(self, order_sn: str, reason: str) -> dict:
        """创建售后（退款）单。

        状态机与 mock 一致：
        - 未知订单 → 拒绝（订单不存在）
        - 待付款 → 拒绝（未支付不可售后）
        - 待发货 / 已发货 / 已完成 → 创建售后单，refund_id = AF{order_sn}
        - 重复申请 → 返回已存在的售后单（幂等，order_sn UNIQUE 约束兜底）
        """
        try:
            async with async_session() as session:
                order = await session.get(MallOrder, order_sn)
                if order is None:
                    return {
                        "refund_id": None,
                        "status": "failed",
                        "message": f"订单 {order_sn} 不存在，无法申请退款",
                    }
                if order.status not in _REFUNDABLE_STATUSES:
                    return {
                        "refund_id": None,
                        "status": "failed",
                        "message": f"订单 {order_sn} 待付款，请先完成支付后再申请退款",
                    }

                existing = await session.get(MallRefund, f"AF{order_sn}")
                if existing is not None:
                    return {
                        "refund_id": existing.refund_id,
                        "status": existing.status,
                        "message": f"订单 {order_sn} 已申请过售后（{existing.refund_id}），请勿重复提交",
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
