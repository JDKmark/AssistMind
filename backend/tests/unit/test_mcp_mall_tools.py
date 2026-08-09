"""MCP 电商业务工具单元测试。

覆盖 4 个工具函数（均为普通 async 函数，可直接 await）：
1. query_order 已知/未知订单（未知返回 {error: "订单不存在"}）
2. query_logistics 已发货有轨迹 / 未发货与未知订单返回空列表
3. query_product 已知/未知商品（未知返回 {error: "商品不存在"}）
4. apply_refund 状态机各分支（待付款拒绝 / 成功创建 / 重复申请幂等 / 未知订单拒绝）

测试策略：直接调用 app.core.mcp.server 中的工具函数（@mcp.tool() 装饰器返回原函数），
数据经 mall 门面（app.core.mall.data_source）落到 mock 数据源；退款类测试用
reset_source() 隔离进程内售后单记录（与 test_mall_data_source.py 一致）。
"""

from __future__ import annotations

from app.core.mall import data_source as ds
from app.core.mcp.server import apply_refund, query_logistics, query_order, query_product

# ---------- 1. query_order ----------


async def test_query_order_known():
    """已知订单返回完整订单信息（状态/明细/实付/物流单号）。"""
    order = await query_order("20240801001")
    assert order["order_sn"] == "20240801001"
    assert order["status"] == "已发货"
    assert order["pay_amount"] == 6999
    assert order["logistics_no"] == "SF1234567890"
    assert order["items"][0]["product_id"] == "P001"
    assert "error" not in order


async def test_query_order_pending_delivery():
    """待发货订单：两件商品，实付 5398，无物流单号。"""
    order = await query_order("20240801002")
    assert order["status"] == "待发货"
    assert order["pay_amount"] == 5398
    assert [(i["product_id"], i["quantity"]) for i in order["items"]] == [
        ("P002", 1),
        ("P004", 1),
    ]
    assert order["logistics_no"] is None


async def test_query_order_unknown():
    """未知订单号返回 {error: "订单不存在"}。"""
    result = await query_order("99999999999")
    assert result == {"error": "订单不存在"}


# ---------- 2. query_logistics ----------


async def test_query_logistics_shipped_order():
    """已发货订单返回固定轨迹（已揽收 → 运输中）。"""
    tracks = await query_logistics("20240801001")
    assert tracks == [
        {"ts": "2024-08-01 16:00:00", "content": "已揽收"},
        {"ts": "2024-08-01 18:30:00", "content": "运输中（预计明天送达）"},
    ]


async def test_query_logistics_not_shipped_or_unknown():
    """未发货订单与未知订单返回空列表，不抛异常。"""
    assert await query_logistics("20240801002") == []
    assert await query_logistics("20240801004") == []
    assert await query_logistics("99999999999") == []


# ---------- 3. query_product ----------


async def test_query_product_known():
    """已知商品返回完整商品信息。"""
    product = await query_product("P001")
    assert product == {
        "id": "P001",
        "name": "华为 Mate 60 Pro",
        "spec": "256G 雅丹黑",
        "price": 6999,
        "stock": 200,
        "services": ["无忧退货", "免费包邮"],
    }


async def test_query_product_with_services():
    """P003 服务标识：无忧退货/快速退款/免费包邮。"""
    product = await query_product("P003")
    assert product["services"] == ["无忧退货", "快速退款", "免费包邮"]
    assert product["price"] == 4990


async def test_query_product_unknown():
    """未知商品返回 {error: "商品不存在"}。"""
    result = await query_product("P999")
    assert result == {"error": "商品不存在"}


# ---------- 4. apply_refund 状态机 ----------


async def test_apply_refund_rejects_unpaid():
    """待付款订单拒绝退款（refund_id=None, status=failed）。"""
    result = await apply_refund("20240801004", "不想要了")
    assert result["refund_id"] is None
    assert result["status"] == "failed"
    assert "待付款" in result["message"]


async def test_apply_refund_creates():
    """可售后状态（待发货）成功创建售后单。"""
    ds.reset_source()
    result = await apply_refund("20240801002", "七天无理由退货")
    assert result["refund_id"] == "AF20240801002"
    assert result["status"] == "处理中"
    assert "已提交" in result["message"]


async def test_apply_refund_shipped_creates():
    """已发货订单成功创建售后单。"""
    ds.reset_source()
    result = await apply_refund("20240801001", "商品质量问题")
    assert result["refund_id"] == "AF20240801001"
    assert result["status"] == "处理中"


async def test_apply_refund_duplicate_idempotent():
    """同一订单重复申请幂等：返回已存在的售后单。"""
    ds.reset_source()
    first = await apply_refund("20240801001", "商品质量问题")
    second = await apply_refund("20240801001", "商品质量问题")
    assert second["refund_id"] == first["refund_id"] == "AF20240801001"
    assert "已申请过售后" in second["message"]


async def test_apply_refund_unknown_order():
    """未知订单拒绝退款并提示订单不存在。"""
    result = await apply_refund("99999999999", "测试")
    assert result["refund_id"] is None
    assert result["status"] == "failed"
    assert "不存在" in result["message"]
