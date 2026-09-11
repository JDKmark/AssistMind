"""T8 Redis 故障下限流兜底单元测试。

核心约束：默认 ``RATE_LIMIT_FALLBACK=off`` 必须保持既有 fail-open 语义完全不变；
仅当显式 ``inproc`` 时，Redis 不可用才用进程内固定窗口兜底（单实例）。

覆盖：
1. off（默认）：Redis 挂 → 全部放行（与既有 test_rate_limit.py 一致）
2. inproc：Redis 挂 → 仍能限流（超限 429 + Retry-After），且 warning 只打一次
3. inproc：Redis 正常 → 走 Redis 计数（兜底不介入）
4. 运行时热切换（settings.RATE_LIMIT_FALLBACK=inproc）生效
5. 进程内计数窗口切换清零
6. check_fixed_window 不传 fallback 时行为与改动前一致（向后兼容）
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.infra import rate_limit as rl
from app.core.infra.rate_limit import RateLimitMiddleware, _InprocFixedWindow


class FakeRedis:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self._counts: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        if self.fail:
            return 0  # RedisClient 未连接/失败语义
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return True


def _make_client(redis: FakeRedis, *, limit: int = 2, fallback: str | None = "off") -> TestClient:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, redis=redis, limit=limit, fallback=fallback)

    @app.get("/echo")
    async def echo():
        return {"ok": True}

    return TestClient(app)


def _reset_warning_flag() -> None:
    rl._warned_inproc_fallback = False


def test_off_is_fail_open_unchanged():
    """默认 off：Redis 挂 → 全部放行（不得改变既有行为）。"""
    _reset_warning_flag()
    client = _make_client(FakeRedis(fail=True), limit=2, fallback="off")
    for _ in range(6):
        assert client.get("/echo").status_code == 200


def test_inproc_fallback_limits_when_redis_down(caplog):
    """inproc：Redis 挂仍能限流（limit+1 次 429），且启用 warning 只打一次。"""
    _reset_warning_flag()
    client = _make_client(FakeRedis(fail=True), limit=2, fallback="inproc")
    with caplog.at_level(logging.WARNING, logger="app.core.infra.rate_limit"):
        assert client.get("/echo").status_code == 200
        assert client.get("/echo").status_code == 200
        resp = client.get("/echo")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    warns = [r for r in caplog.records if "进程内兜底限流" in r.getMessage()]
    assert len(warns) == 1  # 只告警一次，不逐请求刷屏


def test_inproc_does_not_override_healthy_redis():
    """inproc：Redis 正常时仍走 Redis 计数（兜底不介入），限流照常生效。"""
    _reset_warning_flag()
    redis = FakeRedis()
    client = _make_client(redis, limit=2, fallback="inproc")
    assert client.get("/echo").status_code == 200
    assert client.get("/echo").status_code == 200
    assert client.get("/echo").status_code == 429
    assert redis.expire_calls  # 走了 Redis 路径（首次计数设置 EXPIRE）


def test_fallback_hot_toggle_via_settings(monkeypatch):
    """fallback=None 时运行时读 settings：切 inproc 后 Redis 挂也能限流。"""
    _reset_warning_flag()
    monkeypatch.setattr(rl.settings, "RATE_LIMIT_FALLBACK", "off")
    client = _make_client(FakeRedis(fail=True), limit=1, fallback=None)
    for _ in range(4):
        assert client.get("/echo").status_code == 200  # off：fail-open

    monkeypatch.setattr(rl.settings, "RATE_LIMIT_FALLBACK", "inproc")
    assert client.get("/echo").status_code == 200
    assert client.get("/echo").status_code == 429  # 热切换立即生效（无需重启）


def test_inproc_window_resets_on_window_change():
    """进程内固定窗口：窗口号变化时计数清零。"""
    counter = _InprocFixedWindow()
    assert counter.incr("k:1", window=1) == 1
    assert counter.incr("k:1", window=1) == 2
    assert counter.incr("k:1", window=2) == 1  # 新窗口重新计数


async def test_check_fixed_window_without_fallback_unchanged():
    """不传 fallback：Redis 挂 → 放行（向后兼容，既有单测语义不变）。"""
    redis = FakeRedis(fail=True)
    allowed, count = await rl.check_fixed_window(
        redis, key_prefix="rl", identity="1.1.1.1", limit=2, period=60
    )
    assert allowed is True and count == 0


async def test_check_fixed_window_with_fallback_counts():
    """传 fallback：Redis 挂 → 进程内计数并据此裁决。"""
    redis = FakeRedis(fail=True)
    counter = _InprocFixedWindow()
    results = [
        await rl.check_fixed_window(
            redis, key_prefix="rl", identity="1.1.1.1", limit=2, period=60, fallback=counter
        )
        for _ in range(3)
    ]
    assert [ok for ok, _ in results] == [True, True, False]
    assert [c for _, c in results] == [1, 2, 3]
