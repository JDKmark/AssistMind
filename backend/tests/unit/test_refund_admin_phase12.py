from __future__ import annotations

import pytest

from app.core.mall.mock_source import MockMallDataSource
from app.core.mall.real_source import RealMallDataSource


async def _create_refund(source):
    return await source.apply_refund(
        "20240801001",
        "商品质量问题",
        requester_username="user1",
        requester_role="user",
    )


async def test_mock_refunds_can_be_listed_and_filtered():
    source = MockMallDataSource()
    await _create_refund(source)

    result = await source.list_refunds(owner_username="user1", status="处理中")

    assert result["total"] == 1
    assert result["refunds"] == [
        {
            "refund_id": "AF20240801001",
            "order_sn": "20240801001",
            "owner_username": "user1",
            "reason": "商品质量问题",
            "status": "处理中",
            "created_at": "2024-08-01 09:30:00",
        }
    ]


async def test_mock_refund_status_allows_processing_to_approved_only_once():
    source = MockMallDataSource()
    await _create_refund(source)

    result = await source.update_refund_status("AF20240801001", "已通过")

    assert result["status"] == "已通过"
    with pytest.raises(ValueError, match="非法状态流转"):
        await source.update_refund_status("AF20240801001", "已拒绝")


async def test_real_refund_management_contract_has_same_methods():
    source = RealMallDataSource()

    assert hasattr(source, "list_refunds")
    assert hasattr(source, "update_refund_status")


async def test_real_refund_management_failure_is_degraded(monkeypatch, caplog):
    monkeypatch.setattr(
        "app.core.mall.real_source.async_session",
        lambda: (_ for _ in ()).throw(RuntimeError("postgres down")),
    )

    result = await RealMallDataSource().list_refunds()

    assert result == {"refunds": [], "total": 0, "degraded": ["postgres"]}
    assert "list_refunds 失败" in caplog.text
