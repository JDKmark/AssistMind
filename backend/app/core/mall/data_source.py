"""电商业务数据源门面：统一查询入口（Agent 客服工具的数据后端）。

消费方统一从本模块 import（app.core.mall.data_source），调用必须 await。

当前为单实现（mock 预置演示数据），无配置切换；
后续对接真实 ERP 时，可参照 app.core.ops.data_source 的门面模式增加
real 实现与 MALL_DATA_SOURCE=mock/real/auto 配置切换，消费方代码无需改动。
"""

from __future__ import annotations

import logging

from app.core.mall.base import MallDataSource
from app.core.mall.mock_source import MockMallDataSource

logger = logging.getLogger(__name__)

# 已解析的数据源实例（进程内缓存；测试可调 reset_source() 重置）
_source: MallDataSource | None = None


async def _resolve_source() -> MallDataSource:
    """解析并缓存数据源实现（当前仅 mock；后续可加 real 对接 ERP）。"""
    global _source
    if _source is None:
        _source = MockMallDataSource()
        logger.info("[Mall] 数据源=mock（当前单实现，未配置 real/ERP）")
    return _source


def reset_source() -> None:
    """重置数据源缓存（测试隔离用，售后单记录随之清空）。"""
    global _source
    _source = None


async def query_order(order_sn: str) -> dict | None:
    """查询订单信息。未知 order_sn 返回 None。"""
    return await (await _resolve_source()).query_order(order_sn)


async def query_logistics(order_sn: str) -> list[dict]:
    """查询物流轨迹 [{ts, content}]。未发货/未知订单返回空列表。"""
    return await (await _resolve_source()).query_logistics(order_sn)


async def query_product(product_id: str) -> dict | None:
    """查询商品信息。未知 product_id 返回 None。"""
    return await (await _resolve_source()).query_product(product_id)


async def apply_refund(order_sn: str, reason: str) -> dict:
    """创建售后（退款）单，返回 {refund_id, status, message}。

    待付款 / 未知订单拒绝（refund_id=None, status=failed）；重复申请幂等返回已存在售后单。
    """
    return await (await _resolve_source()).apply_refund(order_sn, reason)
