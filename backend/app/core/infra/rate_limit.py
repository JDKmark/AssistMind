"""基于 Redis 的固定窗口限流中间件（RATE_LIMIT_PER_MINUTE 的消费方）。

设计：
- 固定窗口计数：key = {prefix}:{client_ip}:{窗口号}，INCR + 窗口首次计数时 EXPIRE。
  窗口号 = now // period，窗口切换自然产生新 key，无需精确对齐过期时间
  （固定窗口在窗口边界存在 2 倍突刺，客服/演示场景可接受；
  如需平滑可换滑动窗口 zset，见 infra/redis.py 已有 zadd/zrange）。
- 按客户端 IP 限流（演示环境直连，request.client.host 即客户端；
  生产代理场景可扩展 X-Forwarded-For 或按登录用户维度，key 前缀已预留 scope 位）。
- 降级：Redis 不可用（RedisClient.incr 失败返回 0）→ 放行，不误杀。
  与「Redis 缓存失败跳过缓存直查」同哲学：宁可短暂无防护，不可让用户不可用；
  降级路径的 logger.warning 由 RedisClient 记录，本中间件不重复记。
- 可选兜底（T8）：RATE_LIMIT_FALLBACK=inproc 时，Redis 不可用改用**进程内固定窗口**
  兜底计数（仅单实例有效），避免「缓存失效回源放大 + 限流失效」双杀。默认 off，
  行为与既有 fail-open 完全一致（现有 tests/unit/test_rate_limit.py 全绿）。
- 跳过路径：/api/v1/health（探活）、/mcp（Agent 进程内工具通道，不走公网）。
"""

from __future__ import annotations

import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.core.infra import metrics
from app.core.infra.redis import RedisClient

logger = logging.getLogger(__name__)
settings = get_settings()

# 限流不覆盖的路径前缀（健康探活 / Agent 工具通道 / Prometheus 抓取）
# 注意：/mcp 的限流由 MCP 安全边界（app.core.mcp.security）按身份维度独立执行，
# 全局 IP 限流不再重复计数，避免 Agent 进程内工具通道被公网 IP 维度误伤。
# /metrics 为 Prometheus 定时抓取端点，不应被 IP 限流误杀，也不应污染限流计数。
_DEFAULT_SKIP_PREFIXES = ("/api/v1/health", "/mcp", "/metrics")


# 已告警过的非正 limit 值（limit<=0 只告警一次，不逐请求刷日志）
_warned_disabled_limits: set[int] = set()

# 进程内兜底限流启用告警（只告警一次，Redis 长时间不可用时不刷屏）
_warned_inproc_fallback = False


class _InprocFixedWindow:
    """进程内固定窗口计数器（T8 兜底，仅单实例有效）。

    窗口号变化即整体清空（固定窗口语义，无需逐 key 过期）；
    进程内单线程事件循环访问，无需加锁。
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._window = -1

    def incr(self, key: str, window: int) -> int:
        if window != self._window:
            self._counts.clear()
            self._window = window
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]


async def check_fixed_window(
    redis: RedisClient,
    *,
    key_prefix: str,
    identity: str,
    limit: int,
    period: int,
    fallback: _InprocFixedWindow | None = None,
) -> tuple[bool, int]:
    """固定窗口计数 +1，返回 (是否放行, 窗口内累计次数)。

    - 窗口号 = now // period，窗口切换自然产生新 key，无需精确对齐过期时间
    - Redis 不可用（incr 返回 0）：
        fallback=None → 放行（默认 fail-open，不误杀；warning 由 RedisClient 记录）
        fallback 非空 → 用进程内固定窗口兜底计数（T8，仅单实例有效）
    - limit <= 0 → 放行（配置边界保护，避免零值/负数拒绝全部请求），首次命中记
      warning 提示配置异常（限流整体失效需可观测，不静默）
    - 窗口首次计数时设置 EXPIRE 防 key 残留
    """
    if limit <= 0:
        if limit not in _warned_disabled_limits:
            _warned_disabled_limits.add(limit)
            logger.warning(
                "[RateLimit] limit=%d 非正值，限流已整体停用（每请求直接放行），"
                "请检查限流配置",
                limit,
            )
        return True, 0
    window = int(time.time()) // period
    key = f"{key_prefix}:{identity}:{window}"
    count = await redis.incr(key)
    if count == 0:
        if fallback is None:
            return True, 0
        global _warned_inproc_fallback
        if not _warned_inproc_fallback:
            _warned_inproc_fallback = True
            logger.warning(
                "[RateLimit] Redis 不可用，启用进程内兜底限流 RATE_LIMIT_FALLBACK=inproc"
                "（仅单实例有效，多副本请勿依赖）"
            )
        count = fallback.incr(key, window)
        return count <= limit, count
    if count == 1:
        await redis.expire(key, period * 2)
    return count <= limit, count


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按客户端 IP 的固定窗口限流：窗口内超过 limit 次请求返回 429。"""

    def __init__(
        self,
        app,
        *,
        redis: RedisClient,
        limit: int = 60,
        period: int = 60,
        key_prefix: str = "scqa:rl",
        skip_prefixes: tuple[str, ...] = _DEFAULT_SKIP_PREFIXES,
        fallback: str | None = None,
    ):
        super().__init__(app)
        self.redis = redis
        self.limit = limit
        self.period = period
        self.key_prefix = key_prefix
        self.skip_prefixes = skip_prefixes
        # None → 运行时读 settings.RATE_LIMIT_FALLBACK（可热切换）；显式传值用于测试
        self._fallback_override = fallback
        self._inproc = _InprocFixedWindow()

    def _fallback_counter(self) -> _InprocFixedWindow | None:
        """按配置决定是否启用进程内兜底（默认 off，保持既有 fail-open）。"""
        mode = self._fallback_override
        if mode is None:
            mode = getattr(settings, "RATE_LIMIT_FALLBACK", "off")
        return self._inproc if str(mode).lower() == "inproc" else None

    async def dispatch(self, request: Request, call_next):
        """请求进入路由前检查窗口计数，超限直接 429，不进业务逻辑。"""
        if not self._should_limit(request):
            return await call_next(request)

        allowed, _count = await self._check(request)
        if not allowed:
            retry_after = self.period - int(time.time()) % self.period
            logger.warning(
                "[RateLimit] 触发限流 %s（%d 次/%ds）: %s",
                request.url.path,
                self.limit,
                self.period,
                self._client_scope(request),
            )
            metrics.safe(metrics.inc_rate_limited, "ip")
            return JSONResponse(
                status_code=429,
                content={"detail": f"请求过于频繁（{self.limit} 次/分钟），请稍后再试"},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    def _should_limit(self, request: Request) -> bool:
        path = request.url.path
        return not any(path.startswith(p) for p in self.skip_prefixes)

    def _client_scope(self, request: Request) -> str:
        # 演示环境直连：client.host 即客户端；代理场景可扩展取 X-Forwarded-For 首跳
        return request.client.host if request.client else "unknown"

    async def _check(self, request: Request) -> tuple[bool, int]:
        """窗口计数 +1，返回 (是否放行, 窗口内累计次数)。"""
        scope = self._client_scope(request)
        return await check_fixed_window(
            self.redis,
            key_prefix=self.key_prefix,
            identity=scope,
            limit=self.limit,
            period=self.period,
            fallback=self._fallback_counter(),
        )
