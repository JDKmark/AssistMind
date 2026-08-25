"""FastAPI 依赖注入。

提供当前用户、数据库会话、Redis 客户端等依赖。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.config import get_settings
from app.core.security.auth import decode_access_token

settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]):
    """从 JWT 解析当前用户。"""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未认证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return {
        "username": payload.get("sub", "unknown"),
        "role": payload.get("role", "user"),
        "access_token": token,
    }


async def require_admin(user: Annotated[dict, Depends(get_current_user)]):
    """要求管理员角色（知识库删除/重建等管理操作）。

    角色来自 JWT payload（login 时按 User.role 签发），前端路由守卫的 meta.roles
    与后端此依赖双端校验，单点失效不影响另一侧。
    """
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


async def require_staff(user: Annotated[dict, Depends(get_current_user)]):
    """要求客服或管理员角色（知识库列表等 staff-only 接口）。

    与前端路由守卫（admin/agent 入口）双端一致的收敛点，
    未来其它 staff-only 接口可复用。
    """
    if user.get("role") not in {"agent", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要客服或管理员权限",
        )
    return user
