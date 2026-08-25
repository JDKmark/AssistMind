"""商城管理路由。

GET /api/v1/mall/orders      订单列表（admin）：按归属用户/状态过滤 + 分页
GET /api/v1/mall/my-orders   当前用户订单列表：含商品明细，归属服务端强制
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps import get_current_user, require_admin
from app.core.mall import data_source as mall_ds

router = APIRouter()


class RefundStatusRequest(BaseModel):
    status: str


@router.get("/orders")
async def list_orders_api(
    user: Annotated[dict, Depends(require_admin)],
    owner_username: str | None = Query(None, description="按归属用户过滤"),
    status: str | None = Query(None, description="按订单状态过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """查询订单列表（admin，演示隔离用）。

    数据源失败时返回降级空列表（{"orders": [], "total": 0, "degraded": [...]}），
    不抛 500。
    """
    return await mall_ds.list_orders(
        owner_username=owner_username, status=status, limit=limit, offset=offset
    )


@router.get("/refunds")
async def list_refunds_api(
    _admin: Annotated[dict, Depends(require_admin)],
    status: str | None = Query(None),
    owner_username: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await mall_ds.list_refunds(
        status=status, owner_username=owner_username, limit=limit, offset=offset
    )


@router.patch("/refunds/{refund_id}/status")
async def patch_refund_status(
    refund_id: str,
    req: RefundStatusRequest,
    admin: Annotated[dict, Depends(require_admin)],
):
    from app.core import admin_service

    try:
        return await admin_service.update_refund_status(
            refund_id, req.status, admin.get("username", "")
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("/my-orders")
async def my_orders_api(
    user: Annotated[dict, Depends(get_current_user)],
    status: str | None = Query(None, description="按订单状态过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """查询当前用户的订单列表（含商品明细）。

    归属由服务端强制（requester_username=当前登录用户），不声明 owner 参数，
    客户端无法查询他人工单；数据源失败时返回降级空列表，不抛 500。
    """
    return await mall_ds.my_orders(
        requester_username=user.get("username", ""),
        status=status,
        limit=limit,
        offset=offset,
    )
