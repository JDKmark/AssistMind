"""限流中间件（RateLimitMiddleware）单元测试。

覆盖：
- 窗口内放行：limit 次请求全部 200
- 超限 429：第 limit+1 次返回 429 + Retry-After 头（不进业务逻辑）
- 不同 IP 隔离：各自独立计数，互不拖累
- Redis 不可用降级：放行不误杀（incr 返回 0）
- 跳过路径：health 探活不受限
- 窗口首次计数设置 EXPIRE（防 key 残留）
- main.py 已挂载中间件（RATE_LIMIT_PER_MINUTE 消费方生效）

mock 策略：用内存 FakeRedis 替代 RedisClient（不连真实 Redis），
在独立 FastAPI 实例上挂中间件，避免污染全局 app。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.infra.rate_limit import RateLimitMiddleware


class FakeRedis:
    """内存计数 Redis（对齐 RedisClient.incr/expire 语义）。"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self._counts: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        if self.fail:
            return 0  # RedisClient 未连接/失败时的返回语义
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return True


def _make_app(redis: FakeRedis, limit: int = 2) -> TestClient:
    """独立 FastAPI 实例 + 限流中间件（limit 调小便于测试）。"""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, redis=redis, limit=limit)

    @app.get("/echo")
    async def echo():
        return {"ok": True}

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    return TestClient(app)


def test_within_limit_passes():
    """窗口内 limit 次请求全部放行（200）。"""
    client = _make_app(FakeRedis(), limit=2)
    for _ in range(2):
        resp = client.get("/echo")
        assert resp.status_code == 200


def test_over_limit_returns_429_with_retry_after():
    """第 limit+1 次请求返回 429 + Retry-After 头（不进业务逻辑）。"""
    client = _make_app(FakeRedis(), limit=2)
    for _ in range(2):
        assert client.get("/echo").status_code == 200
    resp = client.get("/echo")
    assert resp.status_code == 429
    assert resp.json()["detail"]
    assert "Retry-After" in resp.headers


def test_429_body_is_not_business_response():
    """超限响应是限流 JSON（无业务 ok 字段），确认未进路由。"""
    client = _make_app(FakeRedis(), limit=1)
    client.get("/echo")
    resp = client.get("/echo")
    assert resp.status_code == 429
    assert "ok" not in resp.json()


def test_different_ips_isolated():
    """不同客户端 IP 各自独立计数，互不拖累。"""
    redis = FakeRedis()
    # 两个 TestClient 用不同 client IP（httpx client 参数）
    client_a = _make_app(redis, limit=1)
    client_b = TestClient(
        _make_app(redis, limit=1).app, client=("2.2.2.2", 50000)
    )
    assert client_a.get("/echo").status_code == 200
    assert client_a.get("/echo").status_code == 429  # A 已超限
    # B 是新 IP，不受 A 影响
    assert client_b.get("/echo").status_code == 200


def test_redis_down_allows_all_requests():
    """Redis 不可用（incr 返回 0）：全部放行，不误杀（降级语义）。"""
    client = _make_app(FakeRedis(fail=True), limit=2)
    for _ in range(5):
        assert client.get("/echo").status_code == 200


def test_skip_path_not_rate_limited():
    """跳过路径（health 探活）：超过 limit 也不限。"""
    client = _make_app(FakeRedis(), limit=1)
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 200
    # 同 client 访问业务路径仍受限
    assert client.get("/echo").status_code == 200
    assert client.get("/echo").status_code == 429


def test_first_window_count_sets_expire():
    """窗口首次计数时设置 EXPIRE（防 key 永久残留），后续不重复设置。"""
    import time

    redis = FakeRedis()
    client = _make_app(redis, limit=5)
    for _ in range(3):
        client.get("/echo")
    assert len(redis.expire_calls) == 1
    key, ttl = redis.expire_calls[0]
    # key 形如 scqa:rl:{client_ip}:{窗口号}，窗口号 = now // period
    window = int(time.time()) // 60
    assert key == f"scqa:rl:testclient:{window}"
    assert ttl == 120  # period * 2


def test_main_app_mounts_rate_limit_middleware():
    """main.py 的全局 app 已挂载限流中间件（RATE_LIMIT_PER_MINUTE 消费方生效）。"""
    from app.main import app

    assert any(m.cls is RateLimitMiddleware for m in app.user_middleware)
