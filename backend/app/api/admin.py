"""管理员 API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, model_validator

from app.api.deps import require_admin
from app.core import admin_service, audit_service

router = APIRouter()


@router.get("/overview")
async def get_overview(_admin: Annotated[dict, Depends(require_admin)]):
    return await admin_service.get_overview()


@router.get("/audit")
async def get_audit_logs(
    _admin: Annotated[dict, Depends(require_admin)],
    actor: str | None = Query(default=None),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return await audit_service.list_logs(
        actor=actor,
        action=action,
        target_type=target_type,
        limit=limit,
        offset=offset,
    )


class UserUpdateRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_any_field(self):
        if self.role is None and self.is_active is None:
            raise ValueError("至少提供 role 或 is_active")
        return self


@router.get("/users")
async def get_users(
    _admin: Annotated[dict, Depends(require_admin)],
    role: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return await admin_service.list_users(role=role, keyword=keyword, limit=limit, offset=offset)


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    req: UserUpdateRequest,
    admin: Annotated[dict, Depends(require_admin)],
):
    try:
        return await admin_service.update_user(
            user_id=user_id,
            operator=admin["username"],
            role=req.role,
            is_active=req.is_active,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
