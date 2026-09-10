"""RealMallDataSource 降级分支与契约单元测试（不依赖 Docker/PostgreSQL）。

覆盖 integration 测不到的 except 分支：
- PostgreSQL 异常 → query_order/query_product 返回 None、query_logistics 返回 []、
  apply_refund 返回 failed dict，且都有 logger.warning（不静默 pass）
- _to_amount 金额归一化（int 保持 int、小数保持 float、NULL → None 不静默转 0）
- apply_refund 非可售后状态文案：待付款与 mock 逐字一致；其他状态动态带状态名
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.core.mall.real_source import RealMallDataSource, _to_amount
from app.models.mall import MallOrder, MallRefund


class BrokenSession:
    """模拟 PostgreSQL 完全不可用（async_session() 即抛异常）。"""

    async def __aenter__(self):
        raise RuntimeError("postgres down")

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """可控的假 session：get 返回预置订单/售后单，未配置则 None。"""

    def __init__(self, order: MallOrder | None = None, refund: MallRefund | None = None):
        self._order = order
        self._refund = refund
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, pk):
        if model is MallOrder:
            return self._order
        if model is MallRefund:
            return self._refund
        return None

    async def execute(self, stmt):
        class Result:
            def __init__(self, value):
                self.value = value

            def scalar_one_or_none(self):
                return self.value

            def scalars(self):
                return self

            def all(self):
                return []

        if self._order is not None and getattr(self._order, "owner_username", None) == "user":
            return Result(self._order)
        return Result(None)

    def add(self, obj):
        self._refund = obj

    async def commit(self):
        self.committed = True


def _make_order(order_sn: str = "20240801001", status: str = "已发货") -> MallOrder:
    return MallOrder(
        order_sn=order_sn,
        status=status,
        pay_amount=Decimal("6999.00"),
        logistics_no="SF123456789",
        created_at=datetime(2024, 8, 1, 10, 0, 0),
        owner_username="user",
    )


# ---------- 降级分支（PostgreSQL 异常） ----------


async def test_query_order_returns_none_on_pg_error(monkeypatch, caplog):
    """PG 异常：query_order 返回 None + logger.warning。"""
    monkeypatch.setattr("app.core.mall.real_source.async_session", BrokenSession)
    result = await RealMallDataSource().query_order(
        "20240801001", requester_username="user", requester_role="user"
    )
    assert result is None
    assert any("query_order 失败" in r.message for r in caplog.records)


async def test_query_logistics_returns_empty_on_pg_error(monkeypatch, caplog):
    """PG 异常：query_logistics 返回 [] + logger.warning。"""
    monkeypatch.setattr("app.core.mall.real_source.async_session", BrokenSession)
    result = await RealMallDataSource().query_logistics(
        "20240801001", requester_username="user", requester_role="user"
    )
    assert result == []
    assert any("query_logistics 失败" in r.message for r in caplog.records)


async def test_query_product_returns_none_on_pg_error(monkeypatch, caplog):
    """PG 异常：query_product 返回 None + logger.warning。"""
    monkeypatch.setattr("app.core.mall.real_source.async_session", BrokenSession)
    result = await RealMallDataSource().query_product("P001")
    assert result is None
    assert any("query_product 失败" in r.message for r in caplog.records)


async def test_apply_refund_returns_failed_on_pg_error(monkeypatch, caplog):
    """PG 异常：apply_refund 返回 failed dict + logger.warning（不抛异常）。"""
    monkeypatch.setattr("app.core.mall.real_source.async_session", BrokenSession)
    result = await RealMallDataSource().apply_refund(
        "20240801001", "不想要了", requester_username="user", requester_role="user"
    )
    assert result["refund_id"] is None
    assert result["status"] == "failed"
    assert "转人工" in result["message"]
    assert any("apply_refund 失败" in r.message for r in caplog.records)


# ---------- _to_amount 金额归一化 ----------


async def test_to_amount_none_returns_none():
    """NULL 金额返回 None（不静默转 0，与「未命中返回 None」契约一致）。"""
    assert _to_amount(None) is None


async def test_to_amount_integer_decimal_returns_int():
    """整数值 Decimal → int（6999 而非 6999.0，JSON 序列化与 mock 一致）。"""
    assert _to_amount(Decimal("6999.00")) == 6999
    assert isinstance(_to_amount(Decimal("6999.00")), int)


async def test_to_amount_fractional_decimal_returns_float():
    """非整数值 Decimal → float。"""
    assert _to_amount(Decimal("6999.50")) == 6999.5


async def test_to_amount_integer_float_returns_int():
    """整数值 float → int。"""
    assert _to_amount(6999.0) == 6999
    assert isinstance(_to_amount(6999.0), int)


# ---------- apply_refund 非可售后状态文案 ----------


async def test_apply_refund_unpaid_message_matches_mock(monkeypatch):
    """待付款订单：文案与 mock_source 逐字一致（「请先完成支付」）。"""
    monkeypatch.setattr(
        "app.core.mall.real_source.async_session",
        lambda: FakeSession(order=_make_order(status="待付款")),
    )
    result = await RealMallDataSource().apply_refund(
        "20240801001", "不想要了", requester_username="user", requester_role="user"
    )
    assert result["status"] == "failed"
    assert result["message"] == "订单 20240801001 待付款，请先完成支付后再申请退款"


async def test_apply_refund_other_status_message_is_dynamic(monkeypatch):
    """非待付款的不可售后状态（如已取消）：文案动态带状态名，不误报「待付款」。"""
    monkeypatch.setattr(
        "app.core.mall.real_source.async_session",
        lambda: FakeSession(order=_make_order(status="已取消")),
    )
    result = await RealMallDataSource().apply_refund(
        "20240801001", "不想要了", requester_username="user", requester_role="user"
    )
    assert result["status"] == "failed"
    assert "已取消" in result["message"]
    assert "待付款" not in result["message"]


async def test_apply_refund_unknown_order_message(monkeypatch):
    """未知订单：拒绝文案含订单号。"""
    monkeypatch.setattr("app.core.mall.real_source.async_session", lambda: FakeSession(order=None))
    result = await RealMallDataSource().apply_refund(
        "20240801999", "不想要了", requester_username="user", requester_role="user"
    )
    assert result["status"] == "failed"
    assert "不存在" in result["message"]
