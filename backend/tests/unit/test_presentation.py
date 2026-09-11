"""商城展示策略单元测试（phase13 Task 4）。

覆盖：
- 库存最小披露：user/agent 仅 stock_status，admin 可看精确 stock
- 订单视图：user 无归属且物流掩码；agent 归属/物流掩码；admin 完整
- 管理端订单行：稳定 owner_user_id + 掩码 username
- 退款视图：reason 手机号/邮箱掩码，username 掩码
- 掩码原语：标识保留末四位、用户名、自由文本手机号/邮箱、库存状态
"""

from __future__ import annotations

from app.core.mall.presentation import (
    admin_order_row,
    mask_free_text,
    mask_identifier,
    mask_username,
    order_view,
    product_view,
    refund_view,
    stock_status,
)

# ---------- 商品 ----------


def test_product_view_user_has_no_stock():
    """user：含 stock_status，不含精确 stock。"""
    view = product_view({"id": "P001", "name": "x", "stock": 200, "services": []}, "user")
    assert view["stock_status"] == "有货"
    assert "stock" not in view


def test_product_view_agent_has_no_stock():
    """agent：含 stock_status，不含精确 stock。"""
    view = product_view({"id": "P001", "name": "x", "stock": 200}, "agent")
    assert view["stock_status"] == "有货"
    assert "stock" not in view


def test_product_view_admin_keeps_stock():
    """admin：保留精确 stock，同时提供 stock_status。"""
    view = product_view({"id": "P001", "name": "x", "stock": 200}, "admin")
    assert view["stock"] == 200
    assert view["stock_status"] == "有货"


def test_product_view_none_returns_none():
    assert product_view(None, "user") is None


# ---------- 订单 ----------


def test_order_view_user_masks_logistics_and_no_owner():
    """user：物流单号仅保留末四位，无 owner_username/owner_user_id。"""
    view = order_view(
        {
            "order_sn": "20260801001",
            "owner_username": "user1",
            "owner_user_id": "uid-user1",
            "logistics_no": "SF1234567890",
        },
        "user",
    )
    assert view["logistics_no"] == "********7890"
    assert "owner_username" not in view
    assert "owner_user_id" not in view


def test_order_view_agent_masks_username_and_logistics():
    """agent：归属用户名掩码、物流掩码、无 owner_user_id。"""
    view = order_view(
        {
            "order_sn": "20260801001",
            "owner_username": "user1",
            "owner_user_id": "uid-user1",
            "logistics_no": "SF1234567890",
        },
        "agent",
    )
    assert view["owner_username"] == "u***"
    assert view["logistics_no"] == "********7890"
    assert "owner_user_id" not in view


def test_order_view_admin_keeps_full():
    """admin：保留完整物流单号与归属字段。"""
    view = order_view(
        {
            "order_sn": "20260801001",
            "owner_username": "user1",
            "owner_user_id": "uid-user1",
            "logistics_no": "SF1234567890",
        },
        "admin",
    )
    assert view["logistics_no"] == "SF1234567890"
    assert view["owner_username"] == "user1"
    assert view["owner_user_id"] == "uid-user1"


def test_order_view_user_none_logistics_kept_none():
    """未发货（logistics_no=None）：保持 None，不掩码为假值。"""
    view = order_view({"order_sn": "20260801002", "logistics_no": None}, "user")
    assert view["logistics_no"] is None


# ---------- 管理端行 ----------


def test_admin_order_row_masks_username_keeps_user_id():
    """管理端订单行（admin 专属端点列表）：用户名掩码展示值（spec：列表 username 仅掩码），
    owner_user_id 保留为稳定关联。"""
    row = admin_order_row(
        {
            "order_sn": "20260801001",
            "owner_username": "user1",
            "owner_user_id": "uid-user1",
            "status": "已发货",
        }
    )
    assert row["owner_username"] == "u***"
    assert row["owner_user_id"] == "uid-user1"


# ---------- 退款 ----------


def test_refund_view_masks_reason_pii_and_username():
    """退款视图：reason 中的手机号/邮箱掩码，username 掩码，admin 保留 owner_user_id。"""
    view = refund_view(
        {
            "refund_id": "AF20260801001",
            "reason": "联系我 13812345678 或 a@example.com",
            "owner_username": "user1",
            "owner_user_id": "uid-user1",
        },
        "admin",
    )
    assert view["reason"] == "联系我 138****5678 或 a***@example.com"
    assert view["owner_username"] == "u***"
    assert view["owner_user_id"] == "uid-user1"


def test_refund_view_non_admin_strips_owner_user_id():
    """非 admin：不输出 owner_user_id（内部稳定字段不外泄）。"""
    view = refund_view(
        {"refund_id": "AF1", "reason": "x", "owner_username": "user1", "owner_user_id": "u1"},
        "agent",
    )
    assert "owner_user_id" not in view
    assert view["owner_username"] == "u***"


def test_refund_view_none_returns_none():
    assert refund_view(None, "admin") is None


# ---------- 掩码原语 ----------


def test_stock_status():
    assert stock_status(0) == "缺货"
    assert stock_status(None) == "缺货"
    assert stock_status(1) == "有货"
    assert stock_status(200) == "有货"


def test_mask_identifier_keeps_last_four():
    assert mask_identifier("SF1234567890") == "********7890"
    assert mask_identifier("ABCD1234") == "****1234"


def test_mask_identifier_short_value_fully_masked():
    assert mask_identifier("AB") == "**"


def test_mask_identifier_none_kept():
    assert mask_identifier(None) is None


def test_mask_username():
    assert mask_username("user1") == "u***"
    assert mask_username("u") == "*"
    assert mask_username(None) is None


def test_mask_free_text_mobile_and_email():
    text = "电话 13812345678，邮箱 zhangsan@example.com"
    masked = mask_free_text(text)
    assert "13812345678" not in masked
    assert "138****5678" in masked
    assert "zhangsan@" not in masked
    assert "z***@example.com" in masked
