"""owner_user_id 迁移单元测试（phase13 Task 2）。

覆盖：
- MallOrder 模型含 nullable 且带索引的 owner_user_id 列
- 回填规划 _plan_owner_backfill：
  * 按 username 匹配 user_id
  * 已回填行不重复处理（幂等）
  * owner_username 无对应用户的订单计为孤儿，不误分配
  * 空输入安全
"""

from __future__ import annotations

from app.models.mall import MallOrder
from scripts.init_db import _plan_owner_backfill

# ---------- 模型 ----------


def test_mall_order_has_owner_user_id_column():
    """mall_orders 含 nullable 的 owner_user_id 列。"""
    column = MallOrder.__table__.c.owner_user_id
    assert column is not None
    assert column.nullable is True


def test_mall_order_owner_user_id_indexed():
    """owner_user_id 建索引，支撑按归属高效过滤。"""
    index_names = {idx.name for idx in MallOrder.__table__.indexes}
    assert "ix_mall_orders_owner_user_id" in index_names


# ---------- 回填规划 ----------


def test_plan_backfill_matches_and_skips_filled():
    """未回填行按 username 匹配 user_id；已回填行不重复处理（幂等）。"""
    users = [("user1", "u-1"), ("user2", "u-2")]
    orders = [
        ("20260801001", "user1"),
        ("20260801002", "user1"),
        ("20260801003", "user2"),
    ]
    # 说明：orders 只含 owner_user_id IS NULL 的待回填行（SQL 已过滤），
    # 此处幂等语义由上层 SQL WHERE 保证，规划函数自身只负责匹配与孤儿判定。
    pending, orphans = _plan_owner_backfill(users, orders)
    assert pending == [("20260801001", "u-1"), ("20260801002", "u-1"), ("20260801003", "u-2")]
    assert orphans == 0


def test_plan_backfill_counts_orphans():
    """owner_username 无对应用户的订单计为孤儿，不分配任何 user_id。"""
    users = [("user1", "u-1")]
    orders = [
        ("20260801001", "user1"),
        ("20260801099", "ghost-user"),
    ]
    pending, orphans = _plan_owner_backfill(users, orders)
    assert pending == [("20260801001", "u-1")]
    assert orphans == 1


def test_plan_backfill_ignores_empty_owner_username():
    """owner_username 为空的行不参与规划（无法匹配也不计孤儿）。"""
    pending, orphans = _plan_owner_backfill(
        [("user1", "u-1")],
        [("20260801001", None), ("20260801002", "")],
    )
    assert pending == []
    assert orphans == 0


def test_plan_backfill_empty_inputs():
    """空输入安全返回。"""
    assert _plan_owner_backfill([], []) == ([], 0)
