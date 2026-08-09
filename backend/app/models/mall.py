"""电商业务数据模型（订单 / 商品 / 物流 / 售后）。

对应 MallDataSource 契约（app/core/mall/base.py），real 实现（real_source.py）
以本模块表为数据后端；mock 实现（mock_source.py）用内存常量演示同一份数据。

字段约定（与契约严格一致，勿改字面值）：
- MallOrder.status 中文枚举：待付款 / 待发货 / 已发货 / 已完成
- MallRefund.status：处理中
- services 用 JSON 存中文服务标识列表（无忧退货 / 快速退款 / 免费包邮）
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class MallProduct(Base):
    """商品表（id 形如 P001）。"""

    __tablename__ = "mall_products"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    spec: Mapped[str] = mapped_column(String(100), default="")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stock: Mapped[int] = mapped_column(default=0)
    services: Mapped[list] = mapped_column(JSON, default=list)  # 中文服务标识列表


class MallOrder(Base):
    """订单表（order_sn 形如 20240801001，状态为中文枚举）。"""

    __tablename__ = "mall_orders"

    order_sn: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # 待付款/待发货/已发货/已完成
    pay_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    logistics_no: Mapped[str | None] = mapped_column(String(64), default=None)  # 未发货为 None
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MallOrderItem(Base):
    """订单商品明细（order_sn 软外键，与工单 user_id 同风格不强约束）。"""

    __tablename__ = "mall_order_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_sn: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    spec: Mapped[str] = mapped_column(String(100), default="")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(default=1)


class MallLogistics(Base):
    """物流轨迹表（按 ts 正序返回契约 [{ts, content}]）。"""

    __tablename__ = "mall_logistics"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_sn: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    content: Mapped[str] = mapped_column(String(200), nullable=False)


class MallRefund(Base):
    """售后单表（refund_id 形如 AF{order_sn}，order_sn 唯一保证幂等）。"""

    __tablename__ = "mall_refunds"

    refund_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_sn: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="处理中")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
