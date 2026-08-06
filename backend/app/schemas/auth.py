"""认证相关 Pydantic 模型。"""

from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """登录请求。"""

    username: str
    password: str


class UserInfo(BaseModel):
    """用户信息。"""

    username: str
    role: str


class TokenResponse(BaseModel):
    """登录返回的 JWT。"""

    access_token: str
    token_type: str = "bearer"
    user: UserInfo
