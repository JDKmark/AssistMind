"""电商业务数据源门面：统一查询入口（Agent 客服工具的数据后端）。

消费方统一从本模块 import（app.core.mall.data_source），调用必须 await。

选择逻辑（MALL_DATA_SOURCE）：
- mock: 恒用内存演示数据（MockMallDataSource，默认）
- real: 恒用 PostgreSQL 实现（RealMallDataSource），单源失败走各方法降级
- auto: 配置了 DATABASE_URL 且 PostgreSQL 健康探测（SELECT 1）通过 → real；
        否则降级 mock（logger.warning），与 app.core.ops.data_source 门面语义一致
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.config import get_settings
from app.core.infra.postgres import engine
from app.core.mall.base import MallDataSource
from app.core.mall.mock_source import MockMallDataSource
from app.core.mall.real_source import RealMallDataSource

logger = logging.getLogger(__name__)
settings = get_settings()

# 已解析的数据源实例与实际生效模式（进程内缓存；测试可调 reset_source() 重置）
_source: MallDataSource | None = None
_source_mode: str | None = None


async def _pg_healthy() -> bool:
    """PostgreSQL 健康探测：SELECT 1（失败走异常，不触发断路器）。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning("[Mall] PostgreSQL 健康探测失败: %s", e)
        return False


async def _resolve_source() -> MallDataSource:
    """按配置解析并缓存数据源实现。"""
    global _source, _source_mode
    if _source is not None:
        return _source
    mode = (settings.MALL_DATA_SOURCE or "mock").lower()
    if mode == "mock":
        _source = MockMallDataSource()
        _source_mode = "mock"
        logger.info("[Mall] 数据源=mock（MALL_DATA_SOURCE=mock）")
    elif mode == "real":
        _source = RealMallDataSource()
        _source_mode = "real"
        logger.info("[Mall] 数据源=real（MALL_DATA_SOURCE=real）")
    else:  # auto
        if await _pg_healthy():
            _source = RealMallDataSource()
            _source_mode = "real"
            logger.info("[Mall] 数据源=real（auto 模式，PostgreSQL 健康探测通过）")
        else:
            _source = MockMallDataSource()
            _source_mode = "mock"
            logger.warning("[Mall] PostgreSQL 不可用，auto 模式降级为 mock")
    return _source


def reset_source() -> None:
    """重置数据源缓存（测试隔离用，售后单记录随之清空）。"""
    global _source, _source_mode
    _source = None
    _source_mode = None


async def get_source_mode() -> str:
    """返回实际生效的数据源模式（mock/real），供日志/展示使用。"""
    await _resolve_source()
    return _source_mode or "mock"


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
