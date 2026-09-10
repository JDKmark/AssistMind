"""认证工具：密码哈希（bcrypt）、JWT 生成与校验。

- hash_password / verify_password：用户密码存储与校验（User.hashed_password）
- create_access_token / decode_access_token：登录签发 JWT（payload 含 sub=username、
  role），消费方为 app.api.deps.get_current_user（OAuth2PasswordBearer + 401 语义）
- JWT_SECRET 有 validate_security() 守卫：生产模式默认值会拒绝启动
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jose import jwt
from passlib.context import CryptContext

from app.config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """密码哈希。"""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码。"""
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """生成 JWT。"""
    to_encode = data.copy()
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """解析 JWT。"""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
