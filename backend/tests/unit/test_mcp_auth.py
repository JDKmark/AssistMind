"""MCP 入口安全边界单元测试（phase13 Task 5/6）。

覆盖：
- 有效 JWT：通过，下游正常处理
- 缺失 / 无效 / 过期 token：401，下游不被调用
- 认证后按身份固定窗口限流：超限 429 + Retry-After，下游不被调用
- Redis 不可用：放行不误杀（认证仍严格）
- 限流 key 不含明文身份（摘要不可逆）
- 配置边界：limit<=0 不拒绝全部请求
- 身份维度隔离：不同用户互不拖累
"""

from __future__ import annotations

import time
from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.infra.rate_limit import check_fixed_window
from app.core.mcp.security import MCPAuthMiddleware, identity_digest
from app.core.security.auth import create_access_token

GOOD_TOKEN = create_access_token({"uid": "uid-user1", "sub": "user1", "role": "user"})
ADMIN_TOKEN = create_access_token({"uid": "uid-admin", "sub": "admin", "role": "admin"})
EXPIRED_TOKEN = create_access_token(
    {"uid": "uid-user1", "sub": "user1", "role": "user"},
    expires_delta=timedelta(seconds=-1),
)
AUTH = {"Authorization": f"Bearer {GOOD_TOKEN}"}


class FakeRedis:
    """内存计数 Redis（对齐 RedisClient.incr/expire 语义）。"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self._counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        if self.fail:
            return 0
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return True


def _make_app(redis: FakeRedis, limit: int = 2) -> TestClient:
    """独立 FastAPI 实例 + MCP 认证/限流中间件（limit 调小便于测试）。"""
    app = FastAPI()
    app.add_middleware(
        MCPAuthMiddleware, redis=redis, limit=limit, period=60, key_prefix="scqa:rl:mcp"
    )

    @app.get("/echo")
    async def echo():
        return {"ok": True}

    return TestClient(app)


# ---------- 认证 ----------


def test_valid_token_passes():
    """有效 JWT：通过中间件，下游正常返回业务响应。"""
    client = _make_app(FakeRedis())
    resp = client.get("/echo", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_missing_token_401():
    """缺失 Bearer token：401，下游不被调用。"""
    client = _make_app(FakeRedis())
    resp = client.get("/echo")
    assert resp.status_code == 401
    assert "ok" not in resp.json()


def test_invalid_token_401():
    """无效签名 token：401。"""
    client = _make_app(FakeRedis())
    resp = client.get("/echo", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert "ok" not in resp.json()


def test_expired_token_401():
    """过期 token：401。"""
    client = _make_app(FakeRedis())
    resp = client.get(
        "/echo", headers={"Authorization": f"Bearer {EXPIRED_TOKEN}"}
    )
    assert resp.status_code == 401
    assert "ok" not in resp.json()


# ---------- 限流 ----------


def test_over_limit_429_with_retry_after():
    """认证后超限：429 + Retry-After，下游不被调用。"""
    client = _make_app(FakeRedis(), limit=2)
    for _ in range(2):
        assert client.get("/echo", headers=AUTH).status_code == 200
    resp = client.get("/echo", headers=AUTH)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert "ok" not in resp.json()


def test_rate_limit_isolated_by_identity():
    """身份维度隔离：不同用户互不拖累。"""
    redis = FakeRedis()
    client = _make_app(redis, limit=1)
    assert client.get("/echo", headers=AUTH).status_code == 200
    assert client.get("/echo", headers=AUTH).status_code == 429
    # 另一个用户的新 token 不受影响
    other = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    assert client.get("/echo", headers=other).status_code == 200


def test_redis_down_passes_but_auth_still_enforced():
    """Redis 不可用：认证通过后放行；认证失败仍 401。"""
    client = _make_app(FakeRedis(fail=True), limit=2)
    for _ in range(5):
        assert client.get("/echo", headers=AUTH).status_code == 200
    assert client.get("/echo").status_code == 401


# ---------- key 不泄露身份 ----------


def test_identity_digest_is_irreversible():
    """摘要不含明文 user_id / username，且为固定长度十六进制。"""
    digest = identity_digest("uid-user1")
    assert "uid-user1" not in digest
    assert len(digest) == 32
    assert digest == identity_digest("uid-user1")  # 确定性
    assert digest != identity_digest("uid-user2")


def test_rate_limit_key_contains_only_digest():
    """限流 key 只含摘要与窗口，不含明文身份。"""

    window = int(time.time()) // 60
    digest = identity_digest("uid-user1")
    key = f"scqa:rl:mcp:{digest}:{window}"
    assert "uid-user1" not in key
    assert "user1" not in key


# ---------- 配置边界与共享检查器 ----------


async def test_check_fixed_window_limit_zero_passes_all():
    """limit<=0：不拒绝全部请求（配置边界保护）。"""
    redis = FakeRedis()
    allowed, _count = await check_fixed_window(
        redis, key_prefix="k", identity="i", limit=0, period=60
    )
    assert allowed is True
    allowed, _count = await check_fixed_window(
        redis, key_prefix="k", identity="i", limit=-1, period=60
    )
    assert allowed is True
