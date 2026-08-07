"""电商业务数据初始化脚本（PostgreSQL real 模式用）。

从 app.core.mall.mock_source 导入固定演示清单（商品/订单/物流）作为**单一数据来源**，
保证 mock 与 real 两种实现的数据永远一致。售后单（refunds）是运行时业务数据，不预置。

幂等：目标表非空则跳过（先跑 scripts/init_db.py 建表）。
用法：python scripts/seed_mall_db.py
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import func, select

from app.core.infra.postgres import async_session, engine
from app.core.mall.mock_source import LOGISTICS, ORDERS, PRODUCTS
from app.models.mall import MallLogistics, MallOrder, MallOrderItem, MallProduct

logger = logging.getLogger(__name__)


def _parse_dt(value: str) -> datetime:
    """契约时间字符串 → datetime（格式与 mock 数据一致：%Y-%m-%d %H:%M:%S）。"""
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


async def seed_mall_data() -> bool:
    """写入演示数据（幂等）。返回是否实际写入（False=表非空跳过）。"""
    async with async_session() as session:
        count = await session.scalar(select(func.count(MallProduct.id)))
        if count:
            logger.info("mall 表已有数据（%s 条商品），跳过 seed", count)
            return False

        for product in PRODUCTS.values():
            session.add(
                MallProduct(
                    id=product["id"],
                    name=product["name"],
                    spec=product["spec"],
                    price=product["price"],
                    stock=product["stock"],
                    services=product["services"],
                )
            )

        for order in ORDERS.values():
            session.add(
                MallOrder(
                    order_sn=order["order_sn"],
                    status=order["status"],
                    pay_amount=order["pay_amount"],
                    logistics_no=order["logistics_no"],
                    created_at=_parse_dt(order["created_at"]),
                )
            )
            for item in order["items"]:
                session.add(
                    MallOrderItem(
                        order_sn=order["order_sn"],
                        product_id=item["product_id"],
                        name=item["name"],
                        spec=item["spec"],
                        price=item["price"],
                        quantity=item["quantity"],
                    )
                )

        for order_sn, traces in LOGISTICS.items():
            for trace in traces:
                session.add(
                    MallLogistics(
                        order_sn=order_sn,
                        ts=_parse_dt(trace["ts"]),
                        content=trace["content"],
                    )
                )

        await session.commit()
        logger.info("mall 演示数据 seed 完成：%s 商品 / %s 订单 / %s 物流轨迹",
                    len(PRODUCTS), len(ORDERS), sum(len(v) for v in LOGISTICS.values()))
        return True


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await seed_mall_data()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
