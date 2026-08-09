"""认证路由：登录、获取当前用户。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.core.infra.postgres import async_session
from app.core.security.auth import create_access_token, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse, UserInfo

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """用户登录：校验密码并签发 JWT。"""
    async with async_session() as session:
        result = await session.execute(select(User).where(User.username == req.username))
        user = result.scalar_one_or_none()
        if user is None or not verify_password(req.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )
        token = create_access_token({"sub": user.username, "role": user.role})
        return TokenResponse(
            access_token=token,
            user=UserInfo(username=user.username, role=user.role),
        )


@router.get("/me")
async def get_me():
    """获取当前用户信息（占位，需配合鉴权依赖）。"""
    return {"username": "placeholder", "role": "user"}
