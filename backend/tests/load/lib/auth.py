"""压测鉴权：登录拿 JWT 并复用。

优先级（按可用性降级，保证「无需 PostgreSQL 也能跑压测」）：
1. 环境变量 ``LOAD_TOKEN``：直接用给定 token（CI 场景预注入）
2. ``POST /api/v1/auth/login``：需 LOAD_USERNAME / LOAD_PASSWORD 且后端有该用户
3. 离线自签 JWT：用 ``JWT_SECRET``（env 或 backend/.env）本地签发 —— 压测推荐，
   不依赖数据库种子数据，也不额外给 PG 增加登录负载

注意：``/api/v1/chat/ask`` 只依赖 JWT 校验与 role（不查库），因此离线自签 token
可完整驱动 faq/chat/task 链路的路由与检索 RBAC。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_TOKEN_CACHE: dict[str, object] = {"token": "", "exp": 0.0}
_DEFAULT_ROLE = os.getenv("LOAD_ROLE", "admin")

# 与 sse_client 一致：默认忽略环境代理（本机代理会把 localhost 请求转发出去导致失败）
_SESSION = requests.Session()
_SESSION.trust_env = os.getenv("LOAD_TRUST_ENV", "0") == "1"


def _read_jwt_secret() -> str:
    """读取 JWT_SECRET：env 优先，其次 backend/.env。"""
    secret = os.getenv("JWT_SECRET", "")
    if secret:
        return secret
    env_path = Path(__file__).resolve().parents[3] / ".env"  # backend/.env
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("JWT_SECRET="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _mint_token() -> str:
    """离线自签 JWT（需 jose + JWT_SECRET）。"""
    from jose import jwt

    secret = _read_jwt_secret()
    if not secret:
        raise RuntimeError("无法获取 JWT_SECRET：请设置环境变量或 backend/.env")
    now = int(time.time())
    payload = {
        "uid": "load-user",
        "sub": os.getenv("LOAD_USERNAME", "load-user"),
        "role": _DEFAULT_ROLE,
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, secret, algorithm=os.getenv("JWT_ALGORITHM", "HS256"))


def _login(base_url: str) -> str | None:
    username = os.getenv("LOAD_USERNAME", "")
    password = os.getenv("LOAD_PASSWORD", "")
    if not (username and password):
        return None
    try:
        resp = _SESSION.post(
            f"{base_url}/api/v1/auth/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("access_token", "")
        logger.warning("[LoadAuth] 登录失败 status=%s，尝试离线自签", resp.status_code)
    except Exception as e:  # noqa: BLE001 - 压测脚本：失败即降级
        logger.warning("[LoadAuth] 登录异常（降级离线自签）: %s", e)
    return None


def get_token(base_url: str, *, force_refresh: bool = False) -> str:
    """获取并缓存 JWT（有效期按 50 分钟缓存，避免每请求重签）。"""
    cached = str(_TOKEN_CACHE.get("token") or "")
    exp = float(_TOKEN_CACHE.get("exp") or 0)
    if cached and not force_refresh and time.time() < exp:
        return cached

    token = os.getenv("LOAD_TOKEN", "") or _login(base_url) or _mint_token()
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["exp"] = time.time() + 3000
    logger.info("[LoadAuth] 已获取压测 token（role=%s, 长度=%d）", _DEFAULT_ROLE, len(token))
    return token
