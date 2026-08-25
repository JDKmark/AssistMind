"""电商业务数据源 PostgreSQL 集成测试（需要 Docker PostgreSQL 运行）。

前置：docker-compose up -d postgres → python scripts/init_db.py → python scripts/seed_mall_db.py

覆盖 real 实现四接口读写 + 契约形状 + 退款幂等 + list_orders 列表契约（真实库验证，不 mock）。
演示账号归属：20240801001/002 → user1；20240801003/004 → user2。
运行：pytest tests -q -m integration（或去掉 -k "not integration"）
"""

from __future__ import annotations

import pytest

from app.core.infra.postgres import engine
from app.core.mall.real_source import RealMallDataSource

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def dispose_engine():
    """每个测试后 dispose engine 清空连接池。

    pytest-asyncio 每个 async 测试跑在独立 event loop，而 engine 是模块级单例——
    不 dispose 时下一个测试会复用上个 loop 建立的连接（asyncpg 报
    "Event loop is closed"）。单测侧用 mock 规避了此问题，集成测试连真实 PG 必须处置。
    """
    yield
    await engine.dispose()


@pytest.fixture
async def real_ds():
    """每次测试用全新实例（无进程内状态，天然隔离）。"""
    return RealMallDataSource()


async def test_query_order_shipped_contract(real_ds):
    """已发货订单：契约形状与固定清单数据一致。"""
    order = await real_ds.query_order(
        "20240801001", requester_username="user1", requester_role="user"
    )
    assert order is not None
    assert order["status"] == "已发货"
    assert order["logistics_no"] == "SF1234567890"
    assert order["pay_amount"] == 6999
    assert order["items"][0]["product_id"] == "P001"
    assert order["items"][0]["price"] == 6999
    assert order["items"][0]["quantity"] == 1
    assert order["created_at"].startswith("2024-08-01")


async def test_query_order_with_multi_items(real_ds):
    """待发货订单含两条明细（P002 + P004）。"""
    order = await real_ds.query_order(
        "20240801002", requester_username="user1", requester_role="user"
    )
    assert order["status"] == "待发货"
    assert [(i["product_id"], i["quantity"]) for i in order["items"]] == [
        ("P002", 1),
        ("P004", 1),
    ]
    assert order["pay_amount"] == 5398
    assert order["logistics_no"] is None


async def test_query_order_unknown_returns_none(real_ds):
    assert (
        await real_ds.query_order(
            "99999999999",
            requester_username="user",
            requester_role="user",
        )
        is None
    )


async def test_customer_cannot_access_another_customer_order(real_ds):
    """user2 越权访问 user1 订单（001）：查单/轨迹为空；user1 对 user2 订单（004）退款被拒。"""
    order = await real_ds.query_order(
        "20240801001",
        requester_username="user2",
        requester_role="user",
    )
    tracks = await real_ds.query_logistics(
        "20240801001",
        requester_username="user2",
        requester_role="user",
    )
    refund = await real_ds.apply_refund(
        "20240801004",
        "越权申请",
        requester_username="user1",
        requester_role="user",
    )

    assert order is None
    assert tracks == []
    assert refund["refund_id"] is None
    assert refund["status"] == "failed"
    assert "待付款" not in refund["message"]


async def test_query_logistics_ordered_by_ts(real_ds):
    """物流轨迹按时间正序，内容与固定清单一致。"""
    tracks = await real_ds.query_logistics(
        "20240801001", requester_username="user1", requester_role="user"
    )
    assert [t["content"] for t in tracks] == ["已揽收", "运输中（预计明天送达）"]
    assert tracks[0]["ts"] <= tracks[1]["ts"]


async def test_query_logistics_empty_for_not_shipped(real_ds):
    assert (
        await real_ds.query_logistics(
            "20240801002", requester_username="user1", requester_role="user"
        )
        == []
    )
    assert (
        await real_ds.query_logistics(
            "99999999999", requester_username="user", requester_role="user"
        )
        == []
    )


async def test_query_product_contract(real_ds):
    product = await real_ds.query_product("P003")
    assert product is not None
    assert product["name"] == "戴森 V12 吸尘器"
    assert product["price"] == 4990
    assert product["stock"] == 80
    assert product["services"] == ["无忧退货", "快速退款", "免费包邮"]


async def test_query_product_unknown_returns_none(real_ds):
    assert await real_ds.query_product("P999") is None


async def test_apply_refund_rejects_unpaid(real_ds):
    result = await real_ds.apply_refund(
        "20240801004", "不想要了", requester_username="user2", requester_role="user"
    )
    assert result["refund_id"] is None
    assert result["status"] == "failed"
    assert "待付款" in result["message"]


async def test_apply_refund_unknown_order(real_ds):
    result = await real_ds.apply_refund(
        "99999999999", "测试", requester_username="user", requester_role="user"
    )
    assert result["status"] == "failed"
    assert "不存在" in result["message"]


async def test_apply_refund_success_and_idempotent(real_ds):
    """真实库验证：首次创建成功，重复申请幂等返回同一售后单。"""
    first = await real_ds.apply_refund(
        "20240801003", "重复购买", requester_username="user2", requester_role="user"
    )
    assert first["refund_id"] == "AF20240801003"
    assert first["status"] == "处理中"

    second = await real_ds.apply_refund(
        "20240801003", "重复购买", requester_username="user2", requester_role="user"
    )
    assert second["refund_id"] == first["refund_id"] == "AF20240801003"
    assert "已申请过售后" in second["message"]


# ---- list_orders 契约（真实库） ----


async def test_list_orders_contract(real_ds):
    """全量列表：total=4，created_at 倒序，列表项含 owner_username。"""
    result = await real_ds.list_orders()
    assert result["total"] == 4
    assert [o["order_sn"] for o in result["orders"]] == [
        "20240801004",
        "20240801003",
        "20240801002",
        "20240801001",
    ]
    first = result["orders"][0]
    assert set(first.keys()) == {
        "order_sn",
        "owner_username",
        "status",
        "pay_amount",
        "logistics_no",
        "created_at",
    }


async def test_list_orders_filter_by_owner(real_ds):
    """owner 过滤：user1 → 001/002 两笔，total 与列表一致。"""
    result = await real_ds.list_orders(owner_username="user1")
    assert result["total"] == 2
    assert {o["order_sn"] for o in result["orders"]} == {"20240801001", "20240801002"}
    assert all(o["owner_username"] == "user1" for o in result["orders"])


async def test_source_mode_is_real(real_ds):
    assert real_ds.source_mode == "real"


# ---- my_orders 契约（真实库） ----


async def test_my_orders_contract(real_ds):
    """user1：2 单含 items（倒序），列表项字段完整且 items[0] 有 name。"""
    result = await real_ds.my_orders(requester_username="user1")
    assert result["total"] == 2
    assert [o["order_sn"] for o in result["orders"]] == ["20240801002", "20240801001"]
    first = result["orders"][0]
    assert set(first.keys()) == {
        "order_sn",
        "status",
        "pay_amount",
        "logistics_no",
        "created_at",
        "items",
    }
    assert first["status"] == "待发货"
    assert first["pay_amount"] == 5398
    assert [(i["product_id"], i["quantity"]) for i in first["items"]] == [
        ("P002", 1),
        ("P004", 1),
    ]
    assert first["items"][0]["name"] == "小米电视 65 英寸"
    assert first["items"][0]["spec"] == "65英寸"
    assert first["items"][0]["price"] == 3499


async def test_my_orders_unknown_requester_empty(real_ds):
    """agent（无订单）返回空列表。"""
    result = await real_ds.my_orders(requester_username="agent")
    assert result["orders"] == []
    assert result["total"] == 0
