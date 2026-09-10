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
- 跳过路径：/api/v1/health（探活）、/mcp（Agent 进程内工具通道，不走公网）。
"""

from __future__ import annotations

import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.infra.redis import RedisClient

logger = logging.getLogger(__name__)

# 限流不覆盖的路径前缀（健康探活 / Agent 工具通道）
_DEFAULT_SKIP_PREFIXES = ("/api/v1/health", "/mcp")


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
    ):
        super().__init__(app)
        self.redis = redis
        self.limit = limit
        self.period = period
        self.key_prefix = key_prefix
        self.skip_prefixes = skip_prefixes

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
        window = int(time.time()) // self.period
        key = f"{self.key_prefix}:{scope}:{window}"
        count = await self.redis.incr(key)
        if count == 0:
            # Redis 不可用（RedisClient 已记 warning）：放行，不误杀
            return True, 0
        if count == 1:
            # 窗口首次计数：设置过期防 key 永久残留（失败仅警告，不阻塞）
            await self.redis.expire(key, self.period * 2)
        return count <= self.limit, count
