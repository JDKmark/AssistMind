"""Redis 异步客户端 + 分布式锁。

connect() 失败必须清空 _pool=None，否则 is_connected 误报（吸取原项目教训）。
"""

from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class RedisClient:
    """Redis 异步客户端单例。"""

    def __init__(self) -> None:
        self._pool: redis.ConnectionPool | None = None
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        try:
            self._pool = redis.ConnectionPool.from_url(
                settings.REDIS_URL, decode_responses=True
            )
            self._client = redis.Redis(connection_pool=self._pool)
            await self._client.ping()
            logger.info("[Redis] 连接成功")
        except Exception as e:
            self._pool = None
            self._client = None
            logger.warning("[Redis] 连接失败: %s", e)

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> redis.Redis | None:
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
        if self._pool:
            await self._pool.aclose()
        self._client = None
        self._pool = None

    async def get(self, key: str) -> str | None:
        if not self._client:
            return None
        try:
            return await self._client.get(key)
        except Exception as e:
            logger.warning("[Redis] GET %s 失败: %s", key, e)
            return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        if not self._client:
            return False
        try:
            await self._client.set(key, value, ex=ttl)
            return True
        except Exception as e:
            logger.warning("[Redis] SET %s 失败: %s", key, e)
            return False

    async def incr(self, key: str) -> int:
        if not self._client:
            return 0
        try:
            return await self._client.incr(key)
        except Exception as e:
            logger.warning("[Redis] INCR %s 失败: %s", key, e)
            return 0

    async def expire(self, key: str, ttl: int) -> bool:
        if not self._client:
            return False
        try:
            return bool(await self._client.expire(key, ttl))
        except Exception as e:
            logger.warning("[Redis] EXPIRE %s 失败: %s", key, e)
            return False

    async def zadd(self, key: str, mapping: dict[str, float]) -> bool:
        if not self._client:
            return False
        try:
            await self._client.zadd(key, mapping)
            return True
        except Exception as e:
            logger.warning("[Redis] ZADD %s 失败: %s", key, e)
            return False

    async def zrange(self, key: str, start: int, end: int) -> list[str]:
        if not self._client:
            return []
        try:
            return await self._client.zrange(key, start, end)  # type: ignore[return-value]
        except Exception as e:
            logger.warning("[Redis] ZRANGE %s 失败: %s", key, e)
            return []

    async def hset(self, name: str, mapping: dict[str, Any]) -> bool:
        if not self._client:
            return False
        try:
            await self._client.hset(name, mapping=mapping)
            return True
        except Exception as e:
            logger.warning("[Redis] HSET %s 失败: %s", name, e)
            return False

    async def hgetall(self, name: str) -> dict[str, str]:
        if not self._client:
            return {}
        try:
            return await self._client.hgetall(name)  # type: ignore[return-value]
        except Exception as e:
            logger.warning("[Redis] HGETALL %s 失败: %s", name, e)
            return {}


_redis_client: RedisClient | None = None


def get_redis() -> RedisClient:
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
    return _redis_client
