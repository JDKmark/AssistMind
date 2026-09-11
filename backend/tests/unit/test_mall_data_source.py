"""电商业务数据源单元测试（mock 模式，经门面调用）。

覆盖：
- 查询已知/未知订单（固定清单数据完全一致）
- 物流轨迹（已发货有轨迹，未发货/未知为空）
- 商品信息（固定清单数据完全一致）
- 退款状态机（待付款拒绝、其他状态成功、重复申请幂等、未知订单拒绝）
- list_orders 订单列表（全量/owner 过滤/status 过滤/分页/字段形状）
- 门面转发与 reset_source 隔离
- MALL_DATA_SOURCE 配置切换（mock/real/auto 与健康探测降级）

演示账号归属：20260801001/002 → user1；20260801003/004 → user2。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.config import Settings
from app.core.mall import data_source as ds


async def test_query_order_shipped():
    """已发货订单：固定清单数据完全一致。"""
    order = await ds.query_order("20260801001", requester_user_id="uid-user1", requester_username="user1", requester_role="user")
    assert order == {
        "order_sn": "20260801001",
        "status": "已发货",
        "items": [
            {
                "product_id": "P001",
                "name": "华为 Mate 70 Pro",
                "spec": "256G 曜石黑",
                "price": 6999,
                "quantity": 1,
            }
        ],
        "pay_amount": 6999,
        "logistics_no": "********7890",
        "created_at": "2026-08-01 09:30:00",
    }


async def test_query_order_pending_delivery():
    """待发货订单：P002×1 + P004×1 实付 5398，无物流单号。"""
    order = await ds.query_order("20260801002", requester_user_id="uid-user1", requester_username="user1", requester_role="user")
    assert order["status"] == "待发货"
    assert order["pay_amount"] == 5398
    assert [(i["product_id"], i["quantity"]) for i in order["items"]] == [
        ("P002", 1),
        ("P004", 1),
    ]
    assert order["logistics_no"] is None


async def test_query_order_completed_and_unpaid():
    """已完成/待付款订单状态正确。"""
    assert (await ds.query_order("20260801003", requester_user_id="uid-user2", requester_username="user2", requester_role="user"))[
        "status"
    ] == "已完成"
    assert (await ds.query_order("20260801004", requester_user_id="uid-user2", requester_username="user2", requester_role="user"))[
        "status"
    ] == "待付款"


async def test_query_order_unknown_returns_none():
    """未知订单号返回 None 不抛异常。"""
    assert (
        await ds.query_order("99999999999", requester_user_id="uid-user", requester_username="user", requester_role="user")
        is None
    )


async def test_query_logistics_shipped_order():
    """已发货订单返回固定轨迹：已揽收 → 运输中（预计明天送达）。"""
    tracks = await ds.query_logistics(
        "20260801001", requester_user_id="uid-user1", requester_username="user1", requester_role="user"
    )
    assert tracks == [
        {"ts": "2026-08-01 16:00:00", "content": "已揽收"},
        {"ts": "2026-08-01 18:30:00", "content": "运输中（预计明天送达）"},
    ]


async def test_query_logistics_not_shipped_or_unknown():
    """未发货订单与未知订单返回空列表。"""
    assert (
        await ds.query_logistics("20260801002", requester_user_id="uid-user1", requester_username="user1", requester_role="user")
        == []
    )
    assert (
        await ds.query_logistics("20260801004", requester_user_id="uid-user2", requester_username="user2", requester_role="user")
        == []
    )
    assert (
        await ds.query_logistics("99999999999", requester_user_id="uid-user", requester_username="user", requester_role="user")
        == []
    )


async def test_query_product_p001():
    """P001 商品信息（user 视角：无精确库存，仅 stock_status）。"""
    product = await ds.query_product("P001", requester_role="user")
    assert product == {
        "id": "P001",
        "name": "华为 Mate 70 Pro",
        "spec": "256G 曜石黑",
        "price": 6999,
        "stock_status": "有货",
        "services": ["无忧退货", "免费包邮"],
    }


async def test_query_product_admin_keeps_stock():
    """admin 视角：保留精确 stock。"""
    product = await ds.query_product("P001", requester_role="admin")
    assert product["stock"] == 200
    assert product["stock_status"] == "有货"


async def test_query_product_p003_services():
    """P003 服务标识：无忧退货/快速退款/免费包邮（agent 视角无精确库存）。"""
    product = await ds.query_product("P003", requester_role="agent")
    assert product["services"] == ["无忧退货", "快速退款", "免费包邮"]
    assert product["price"] == 4990
    assert "stock" not in product
    assert product["stock_status"] == "有货"


async def test_query_product_all_fixed_catalog():
    """固定清单 5 个商品均可查到。"""
    for pid in ["P001", "P002", "P003", "P004", "P005"]:
        assert await ds.query_product(pid, requester_role="user") is not None


async def test_query_product_unknown_returns_none():
    """未知商品返回 None 不抛异常。"""
    assert await ds.query_product("P999", requester_role="user") is None


async def test_apply_refund_rejects_unpaid():
    """待付款订单拒绝退款并提示原因。"""
    result = await ds.apply_refund(
        "20260801004", "不想要了", requester_user_id="uid-user2", requester_username="user2", requester_role="user"
    )
    assert result["refund_id"] is None
    assert result["status"] == "failed"
    assert "待付款" in result["message"]


async def test_apply_refund_pending_delivery():
    """待发货订单可申请退款。"""
    ds.reset_source()
    result = await ds.apply_refund(
        "20260801002", "七天无理由退货", requester_user_id="uid-user1", requester_username="user1", requester_role="user"
    )
    assert result["refund_id"] == "AF20260801002"
    assert result["status"] == "处理中"
    assert "已提交" in result["message"]


async def test_apply_refund_shipped():
    """已发货订单可申请退款。"""
    ds.reset_source()
    result = await ds.apply_refund(
        "20260801001", "商品质量问题", requester_user_id="uid-user1", requester_username="user1", requester_role="user"
    )
    assert result["refund_id"] == "AF20260801001"
    assert result["status"] == "处理中"


async def test_apply_refund_completed():
    """已完成订单可申请退款。"""
    ds.reset_source()
    result = await ds.apply_refund(
        "20260801003", "重复购买", requester_user_id="uid-user2", requester_username="user2", requester_role="user"
    )
    assert result["refund_id"] == "AF20260801003"
    assert result["status"] == "处理中"


async def test_apply_refund_duplicate_is_idempotent():
    """同一订单重复申请返回已存在的售后单，不重复创建。"""
    ds.reset_source()
    first = await ds.apply_refund(
        "20260801001", "商品质量问题", requester_user_id="uid-user1", requester_username="user1", requester_role="user"
    )
    second = await ds.apply_refund(
        "20260801001", "商品质量问题", requester_user_id="uid-user1", requester_username="user1", requester_role="user"
    )
    assert second["refund_id"] == first["refund_id"] == "AF20260801001"
    assert "已申请过售后" in second["message"]


async def test_apply_refund_unknown_order():
    """未知订单拒绝退款并提示订单不存在。"""
    result = await ds.apply_refund(
        "99999999999", "测试", requester_user_id="uid-user", requester_username="user", requester_role="user"
    )
    assert result["refund_id"] is None
    assert result["status"] == "failed"
    assert "不存在" in result["message"]


# ---- list_orders 订单列表 ----


async def test_list_orders_all():
    """全量列表：total=4，按 created_at 倒序。"""
    result = await ds.list_orders()
    assert result["total"] == 4
    assert [o["order_sn"] for o in result["orders"]] == [
        "20260801004",
        "20260801003",
        "20260801002",
        "20260801001",
    ]


async def test_list_orders_filter_by_owner():
    """owner 过滤：user1 → 001/002 两笔。"""
    result = await ds.list_orders(owner_username="user1")
    assert result["total"] == 2
    assert {o["order_sn"] for o in result["orders"]} == {"20260801001", "20260801002"}


async def test_list_orders_filter_by_status():
    """status 过滤：已发货 → 仅 001。"""
    result = await ds.list_orders(status="已发货")
    assert result["total"] == 1
    assert result["orders"][0]["order_sn"] == "20260801001"


async def test_list_orders_pagination():
    """分页：total 为过滤后总数（与分页无关），orders 按倒序切片。"""
    page1 = await ds.list_orders(limit=2, offset=0)
    assert page1["total"] == 4
    assert [o["order_sn"] for o in page1["orders"]] == ["20260801004", "20260801003"]

    page2 = await ds.list_orders(limit=2, offset=2)
    assert page2["total"] == 4
    assert [o["order_sn"] for o in page2["orders"]] == ["20260801002", "20260801001"]


async def test_list_orders_item_shape():
    """列表项字段形状：admin 行含稳定 owner_user_id，username 为掩码展示值（spec 对齐）。"""
    result = await ds.list_orders(owner_username="user1", status="已发货")
    assert result["total"] == 1
    assert result["orders"][0] == {
        "order_sn": "20260801001",
        "owner_username": "u***",
        "owner_user_id": "uid-user1",
        "status": "已发货",
        "pay_amount": 6999,
        "logistics_no": "SF1234567890",
        "created_at": "2026-08-01 09:30:00",
    }


# ---- my_orders 用户订单列表 ----


async def test_my_orders_user1_two_orders_with_items():
    """user1：2 单（001/002），首单 items 含 product_id/name/spec/price/quantity。"""
    result = await ds.my_orders(requester_user_id="uid-user1", requester_username="user1")
    assert result["total"] == 2
    assert [o["order_sn"] for o in result["orders"]] == ["20260801002", "20260801001"]
    first = result["orders"][0]
    assert first["status"] == "待发货"
    item = first["items"][0]
    assert item == {
        "product_id": "P002",
        "name": "小米电视 65 英寸",
        "spec": "65英寸",
        "price": 3499,
        "quantity": 1,
    }


async def test_my_orders_user2_two_orders():
    """user2：2 单（003/004），created_at 倒序。"""
    result = await ds.my_orders(requester_user_id="uid-user2", requester_username="user2")
    assert result["total"] == 2
    assert [o["order_sn"] for o in result["orders"]] == ["20260801004", "20260801003"]


async def test_my_orders_filter_by_status():
    """status 过滤：user1 + 已发货 → 仅 001。"""
    result = await ds.my_orders(requester_user_id="uid-user1", requester_username="user1", status="已发货")
    assert result["total"] == 1
    assert result["orders"][0]["order_sn"] == "20260801001"


async def test_my_orders_pagination():
    """分页：total 为过滤后总数（与分页无关），orders 按倒序切片。"""
    page = await ds.my_orders(requester_user_id="uid-user1", requester_username="user1", limit=1, offset=1)
    assert page["total"] == 2
    assert [o["order_sn"] for o in page["orders"]] == ["20260801001"]


async def test_my_orders_unknown_requester_empty():
    """无订单用户（agent）返回空列表不抛异常。"""
    result = await ds.my_orders(requester_user_id="uid-agent", requester_username="agent")
    assert result == {"orders": [], "total": 0}


async def test_facade_forwards_to_mock():
    """门面转发：查询/退款均经 mock 实现返回。"""
    assert (await ds.query_product("P005", requester_role="user"))["name"] == "联想拯救者笔记本"
    assert (await ds.query_order("20260801004", requester_user_id="uid-user2", requester_username="user2", requester_role="user"))[
        "status"
    ] == "待付款"
    assert (
        await ds.query_logistics("20260801001", requester_user_id="uid-user1", requester_username="user1", requester_role="user")
    )[0]["content"] == "已揽收"


async def test_reset_source_clears_refund_records():
    """reset_source 后售后记录清空，重复申请按首次创建处理。"""
    ds.reset_source()
    first = await ds.apply_refund(
        "20260801001", "重置测试", requester_user_id="uid-user1", requester_username="user1", requester_role="user"
    )
    assert first["refund_id"] == "AF20260801001"
    ds.reset_source()
    again = await ds.apply_refund(
        "20260801001", "重置测试", requester_user_id="uid-user1", requester_username="user1", requester_role="user"
    )
    assert again["refund_id"] == "AF20260801001"
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
    fake_source.query_order = AsyncMock(return_value={"order_sn": "20260801001"})
    fake_source.apply_refund = AsyncMock(
        return_value={"refund_id": "AF20260801001", "status": "处理中", "message": "ok"}
    )
    monkeypatch.setattr(ds, "_resolve_source", AsyncMock(return_value=fake_source))
    assert (await ds.query_order("20260801001", requester_user_id="uid-user", requester_username="user", requester_role="user"))[
        "order_sn"
    ] == "20260801001"
    assert (
        await ds.apply_refund(
            "20260801001",
            "测试",
            requester_user_id="uid-user",
            requester_username="user",
            requester_role="user",
        )
    )["status"] == "处理中"


async def test_query_order_rejects_non_owner():
    result = await ds.query_order("20260801001", requester_user_id="uid-other", requester_username="other", requester_role="user")
    assert result is None


async def test_query_logistics_rejects_non_owner():
    result = await ds.query_logistics(
        "20260801001", requester_user_id="uid-other", requester_username="other", requester_role="user"
    )
    assert result == []


async def test_apply_refund_rejects_non_owner_without_creating_refund():
    ds.reset_source()
    denied = await ds.apply_refund(
        "20260801001",
        "非本人订单",
        requester_user_id="uid-other",
        requester_username="other",
        requester_role="user",
    )
    allowed = await ds.apply_refund(
        "20260801001",
        "本人申请",
        requester_user_id="uid-user1",
        requester_username="user1",
        requester_role="user",
    )
    assert denied["status"] == "failed"
    assert denied["refund_id"] is None
    assert allowed["status"] == "处理中"
    assert "已提交" in allowed["message"]


async def test_agent_can_access_customer_order():
    result = await ds.query_order("20260801001", requester_user_id="uid-agent", requester_username="agent", requester_role="agent")
    assert result is not None


# ---- phase13：user_id 权威归属（mock 与 real 同语义） ----


async def test_query_order_by_user_id_authoritative():
    """user_id 匹配本人订单成功（owner_user_id 权威）。"""
    order = await ds.query_order(
        "20260801001",
        requester_user_id="uid-user1",
        requester_username="user1",
        requester_role="user",
    )
    assert order is not None
    assert order["order_sn"] == "20260801001"


async def test_query_order_denies_unrelated_user():
    """他人数据拒绝：uid 与 username 均不匹配 → None（真实安全边界）。

    mock/real 语义分工：mock 行的 owner_user_id 是演示静态值（uid-user1 等），
    与真实登录用户的 users 表 UUID 永远不同源，若对 uid 不匹配一票否决，
    演示用户名下将"无订单"（查询/物流/售后全被阻断）——故 mock 按
    owner_username 兜底（见 test_owner_match_uid_or_username_compat 与
    test_query_order_with_real_uuid_username_fallback）；user_id 权威的严格
    语义由 real_source 的 SQL 等值过滤（owner_user_id = :uid）保证。
    """
    result = await ds.query_order(
        "20260801001",
        requester_user_id="uid-other",
        requester_username="user2",
        requester_role="user",
    )
    assert result is None


async def test_query_order_denies_same_user_id_wrong_username():
    """同 user_id 但 username 不匹配：仍放行（user_id 才是归属权威，username 仅供旧行回退）。"""
    from app.core.mall.mock_source import MockMallDataSource

    source = MockMallDataSource()
    order = await source.query_order(
        "20260801001",
        requester_user_id="uid-user1",
        requester_username="someone-else",
        requester_role="user",
    )
    assert order is not None


async def test_owner_match_uid_or_username_compat():
    """mock 归属匹配：真实 uid 命中即放行；mock 静态演示 uid（uid-user1 等）与
    真实登录用户的 users 表 UUID 不同源，不匹配时按 owner_username 兜底，
    否则演示用户名下"无订单"（查询/物流/售后全被阻断）。"""
    from app.core.mall.mock_source import _order_owned_by

    legacy = {"owner_username": "user1", "owner_user_id": None}
    assert _order_owned_by(legacy, "uid-user1", "user1", "user") is True
    assert _order_owned_by(legacy, "uid-other", "user1", "user") is True

    demo = {"owner_username": "user1", "owner_user_id": "uid-user1"}
    assert _order_owned_by(demo, "uid-user1", "user1", "user") is True
    # 真实登录用户 UUID 与静态演示 uid 不同源：username 兜底放行
    assert _order_owned_by(demo, "3f8a0000-0000-0000-0000-000000000001", "user1", "user") is True
    # 非本人（username 也不匹配）：拒绝
    assert _order_owned_by(demo, "3f8a0000-0000-0000-0000-000000000002", "user2", "user") is False


async def test_query_order_with_real_uuid_username_fallback():
    """mock 数据源端到端：真实 UUID 请求者按 username 兜底可见自己的订单。"""
    order = await ds.query_order(
        "20260801001",
        requester_user_id="3f8a0000-0000-0000-0000-000000000001",
        requester_username="user1",
        requester_role="user",
    )
    assert order is not None
    assert order["order_sn"] == "20260801001"


async def test_list_orders_unknown_owner_returns_empty():
    """admin owner_username 筛选：未知用户名返回空列表（不泄露任意匹配）。"""
    result = await ds.list_orders(owner_username="ghost-user")
    assert result == {"orders": [], "total": 0}


async def test_list_refunds_unknown_owner_returns_empty():
    """admin refunds owner_username 筛选：未知用户名返回空列表。"""
    ds.reset_source()
    await ds.apply_refund(
        "20260801001",
        "商品质量问题",
        requester_user_id="uid-user1",
        requester_username="user1",
        requester_role="user",
    )
    result = await ds.list_refunds(owner_username="ghost-user")
    assert result == {"refunds": [], "total": 0}
