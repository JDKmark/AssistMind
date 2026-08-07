"""电商业务数据源单元测试（mock 模式，经门面调用）。

覆盖：
- 查询已知/未知订单（固定清单数据完全一致）
- 物流轨迹（已发货有轨迹，未发货/未知为空）
- 商品信息（固定清单数据完全一致）
- 退款状态机（待付款拒绝、其他状态成功、重复申请幂等、未知订单拒绝）
- 门面转发与 reset_source 隔离
- MALL_DATA_SOURCE 配置切换（mock/real/auto 与健康探测降级）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.config import Settings
from app.core.mall import data_source as ds


async def test_query_order_shipped():
    """已发货订单：固定清单数据完全一致。"""
    order = await ds.query_order("20240801001")
    assert order == {
        "order_sn": "20240801001",
        "status": "已发货",
        "items": [
            {
                "product_id": "P001",
                "name": "华为 Mate 60 Pro",
                "spec": "256G 雅丹黑",
                "price": 6999,
                "quantity": 1,
            }
        ],
        "pay_amount": 6999,
        "logistics_no": "SF1234567890",
        "created_at": "2024-08-01 09:30:00",
    }


async def test_query_order_pending_delivery():
    """待发货订单：P002×1 + P004×1 实付 5398，无物流单号。"""
    order = await ds.query_order("20240801002")
    assert order["status"] == "待发货"
    assert order["pay_amount"] == 5398
    assert [(i["product_id"], i["quantity"]) for i in order["items"]] == [
        ("P002", 1),
        ("P004", 1),
    ]
    assert order["logistics_no"] is None


async def test_query_order_completed_and_unpaid():
    """已完成/待付款订单状态正确。"""
    assert (await ds.query_order("20240801003"))["status"] == "已完成"
    assert (await ds.query_order("20240801004"))["status"] == "待付款"


async def test_query_order_unknown_returns_none():
    """未知订单号返回 None 不抛异常。"""
    assert await ds.query_order("99999999999") is None


async def test_query_logistics_shipped_order():
    """已发货订单返回固定轨迹：已揽收 → 运输中（预计明天送达）。"""
    tracks = await ds.query_logistics("20240801001")
    assert tracks == [
        {"ts": "2024-08-01 16:00:00", "content": "已揽收"},
        {"ts": "2024-08-01 18:30:00", "content": "运输中（预计明天送达）"},
    ]


async def test_query_logistics_not_shipped_or_unknown():
    """未发货订单与未知订单返回空列表。"""
    assert await ds.query_logistics("20240801002") == []
    assert await ds.query_logistics("20240801004") == []
    assert await ds.query_logistics("99999999999") == []


async def test_query_product_p001():
    """P001 商品信息与固定清单完全一致。"""
    product = await ds.query_product("P001")
    assert product == {
        "id": "P001",
        "name": "华为 Mate 60 Pro",
        "spec": "256G 雅丹黑",
        "price": 6999,
        "stock": 200,
        "services": ["无忧退货", "免费包邮"],
    }


async def test_query_product_p003_services():
    """P003 服务标识：无忧退货/快速退款/免费包邮。"""
    product = await ds.query_product("P003")
    assert product["services"] == ["无忧退货", "快速退款", "免费包邮"]
    assert product["price"] == 4990
    assert product["stock"] == 80


async def test_query_product_all_fixed_catalog():
    """固定清单 5 个商品均可查到。"""
    for pid in ["P001", "P002", "P003", "P004", "P005"]:
        assert await ds.query_product(pid) is not None


async def test_query_product_unknown_returns_none():
    """未知商品返回 None 不抛异常。"""
    assert await ds.query_product("P999") is None


async def test_apply_refund_rejects_unpaid():
    """待付款订单拒绝退款并提示原因。"""
    result = await ds.apply_refund("20240801004", "不想要了")
    assert result["refund_id"] is None
    assert result["status"] == "failed"
    assert "待付款" in result["message"]


async def test_apply_refund_pending_delivery():
    """待发货订单可申请退款。"""
    ds.reset_source()
    result = await ds.apply_refund("20240801002", "七天无理由退货")
    assert result["refund_id"] == "AF20240801002"
    assert result["status"] == "处理中"
    assert "已提交" in result["message"]


async def test_apply_refund_shipped():
    """已发货订单可申请退款。"""
    ds.reset_source()
    result = await ds.apply_refund("20240801001", "商品质量问题")
    assert result["refund_id"] == "AF20240801001"
    assert result["status"] == "处理中"


async def test_apply_refund_completed():
    """已完成订单可申请退款。"""
    ds.reset_source()
    result = await ds.apply_refund("20240801003", "重复购买")
    assert result["refund_id"] == "AF20240801003"
    assert result["status"] == "处理中"


async def test_apply_refund_duplicate_is_idempotent():
    """同一订单重复申请返回已存在的售后单，不重复创建。"""
    ds.reset_source()
    first = await ds.apply_refund("20240801001", "商品质量问题")
    second = await ds.apply_refund("20240801001", "商品质量问题")
    assert second["refund_id"] == first["refund_id"] == "AF20240801001"
    assert "已申请过售后" in second["message"]


async def test_apply_refund_unknown_order():
    """未知订单拒绝退款并提示订单不存在。"""
    result = await ds.apply_refund("99999999999", "测试")
    assert result["refund_id"] is None
    assert result["status"] == "failed"
    assert "不存在" in result["message"]


async def test_facade_forwards_to_mock():
    """门面转发：查询/退款均经 mock 实现返回。"""
    assert (await ds.query_product("P005"))["name"] == "联想拯救者笔记本"
    assert (await ds.query_order("20240801004"))["status"] == "待付款"
    assert (await ds.query_logistics("20240801001"))[0]["content"] == "已揽收"


async def test_reset_source_clears_refund_records():
    """reset_source 后售后记录清空，重复申请按首次创建处理。"""
    ds.reset_source()
    first = await ds.apply_refund("20240801001", "重置测试")
    assert first["refund_id"] == "AF20240801001"
    ds.reset_source()
    again = await ds.apply_refund("20240801001", "重置测试")
    assert again["refund_id"] == "AF20240801001"
    assert "已提交" in again["message"]


# ---- MALL_DATA_SOURCE 配置切换 ----

async def test_mode_mock_uses_mock_impl(monkeypatch):
    """MALL_DATA_SOURCE=mock：恒用 MockMallDataSource，source_mode=mock。"""
    from app.core.mall.mock_source import MockMallDataSource

    monkeypatch.setattr(ds, "settings", Settings(MALL_DATA_SOURCE="mock"))
    ds.reset_source()
    await ds._resolve_source()
    assert isinstance(ds._source, MockMallDataSource)
    assert await ds.get_source_mode() == "mock"


async def test_mode_real_uses_real_impl(monkeypatch):
    """MALL_DATA_SOURCE=real：恒用 RealMallDataSource，source_mode=real。"""
    from app.core.mall.real_source import RealMallDataSource

    monkeypatch.setattr(ds, "settings", Settings(MALL_DATA_SOURCE="real"))
    ds.reset_source()
    await ds._resolve_source()
    assert isinstance(ds._source, RealMallDataSource)
    assert await ds.get_source_mode() == "real"


async def test_mode_auto_pg_healthy_uses_real(monkeypatch):
    """MALL_DATA_SOURCE=auto 且健康探测通过：用 real。"""
    from app.core.mall.real_source import RealMallDataSource

    monkeypatch.setattr(ds, "settings", Settings(MALL_DATA_SOURCE="auto"))
    monkeypatch.setattr(ds, "_pg_healthy", AsyncMock(return_value=True))
    ds.reset_source()
    await ds._resolve_source()
    assert isinstance(ds._source, RealMallDataSource)
    assert await ds.get_source_mode() == "real"


async def test_mode_auto_pg_down_falls_back_to_mock(monkeypatch):
    """MALL_DATA_SOURCE=auto 且健康探测失败：降级 mock（不抛异常）。"""
    from app.core.mall.mock_source import MockMallDataSource

    monkeypatch.setattr(ds, "settings", Settings(MALL_DATA_SOURCE="auto"))
    monkeypatch.setattr(ds, "_pg_healthy", AsyncMock(return_value=False))
    ds.reset_source()
    await ds._resolve_source()
    assert isinstance(ds._source, MockMallDataSource)
    assert await ds.get_source_mode() == "mock"


async def test_pg_healthy_returns_false_on_connection_error(monkeypatch):
    """_pg_healthy：PostgreSQL 连接失败返回 False 不抛异常。"""
    mock_engine = MagicMock()
    mock_engine.connect = MagicMock(side_effect=Exception("connection refused"))
    monkeypatch.setattr(ds, "engine", mock_engine)
    assert await ds._pg_healthy() is False


async def test_facade_forwards_through_mode_switch(monkeypatch):
    """门面转发不依赖具体实现：切 real 后调用仍走同一门面函数。"""
    fake_source = MagicMock()
    fake_source.source_mode = "real"
    fake_source.query_order = AsyncMock(return_value={"order_sn": "20240801001"})
    fake_source.apply_refund = AsyncMock(
        return_value={"refund_id": "AF20240801001", "status": "处理中", "message": "ok"}
    )
    monkeypatch.setattr(ds, "_resolve_source", AsyncMock(return_value=fake_source))
    assert (await ds.query_order("20240801001"))["order_sn"] == "20240801001"
    assert (await ds.apply_refund("20240801001", "测试"))["status"] == "处理中"
