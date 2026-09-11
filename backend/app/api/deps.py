"""FastAPI 依赖注入。

提供当前用户、数据库会话、Redis 客户端等依赖。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select

from app.config import get_settings
from app.core.infra.postgres import async_session
from app.core.security.auth import decode_access_token
from app.models.user import User

settings = get_settings()
logger = logging.getLogger(__name__)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def resolve_token_identity(token: str) -> dict:
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    username = payload.get("sub")
    role = payload.get("role")
    user_id = payload.get("uid")
    if not username or not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user_id:
        # 迁移窗口：旧 token 无 uid，按 sub 查询活跃用户补齐 user_id；
        # INFO 级日志用于观测旧 token 存量（迁移结束后可连同本分支移除）
        logger.info("[Identity] 旧 token 无 uid，按 sub=%s 补齐 user_id", username)
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(User).where(User.username == username)
                )
                user = result.scalar_one_or_none()
            if user is None or not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="无效的认证凭证",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            user_id = user.id
            username = user.username
            role = user.role
        except HTTPException:
            raise
        except Exception:
            # PG 故障无法核验旧 token 用户的真实存在性/活跃状态（安全收紧）：
            # 保留 username 自证身份以维持聊天类接口可用（既有降级哲学），
            # 但角色一律降级为 user——被删除/停用的旧管理 token 因此失去
            # require_admin/require_staff 能力，不再能以无法核验的 role 越权。
            logger.warning(
                "[Identity] 旧 token 身份补齐失败（PG 不可用），角色降级为 user：sub=%s",
                username,
                exc_info=True,
            )
            role = "user"
    return {
        "user_id": user_id,
        "username": username,
        "role": role,
        "access_token": token,
    }


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]):
    """从 JWT 解析当前用户。"""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未认证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await resolve_token_identity(token)


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
