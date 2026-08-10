"""健康检查端点：应用状态 + 依赖实际探测（Qdrant / Redis / PostgreSQL / Langfuse）。

探测语义：
- 复用各客户端已建立的连接（lifespan 已 connect），不重复建连
- 探测本身加 2s 超时：健康检查不能被依赖拖慢；失败标记 degraded 不抛异常
- 未启用（PostgreSQL 未配置等）→ "disabled"；连接失败 → "degraded"
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.core.infra.langfuse import is_langfuse_enabled
from app.core.infra.qdrant import get_qdrant
from app.core.infra.redis import get_redis
from app.core.infra.postgres import engine as pg_engine

router = APIRouter()
settings = get_settings()

_PROBE_TIMEOUT = 2.0  # 单依赖探测超时（秒），健康检查不能被依赖拖慢


async def _probe(coro, fallback: str = "degraded") -> str:
    """执行探测协程，超时/异常返回 fallback（不抛异常）。"""
    try:
        return await asyncio.wait_for(coro, timeout=_PROBE_TIMEOUT)
    except Exception:
        return fallback


async def _probe_qdrant() -> str:
    qdrant = get_qdrant()
    # is_connected 反映最近一次 connect 结果；连接断开时补一次探测
    if qdrant.is_connected:
        return "ok"
    await qdrant.connect()
    return "ok" if qdrant.is_connected else "degraded"


async def _probe_redis() -> str:
    redis = get_redis()
    client = redis.client  # property：原始 redis-py 客户端
    if not redis.is_connected or client is None:
        return "degraded"
    try:
        await asyncio.wait_for(client.ping(), timeout=_PROBE_TIMEOUT)
        return "ok"
    except Exception:
        return "degraded"


async def _probe_postgres() -> str:
    if not settings.DATABASE_URL:
        return "disabled"
    try:
        async def _do() -> None:
            async with pg_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        await asyncio.wait_for(_do(), timeout=_PROBE_TIMEOUT)
        return "ok"
    except Exception:
        return "degraded"


@router.get("/health")
async def health():
    """全链路健康检查：应用状态 + 依赖实际探测（Admin 页系统健康标签数据源）。"""
    qdrant, redis, postgres = await asyncio.gather(
        _probe(_probe_qdrant()),
        _probe(_probe_redis()),
        _probe(_probe_postgres()),
    )
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": "0.1.0",
        "debug": settings.DEBUG,
        "dependencies": {
            "qdrant": qdrant,
            "redis": redis,
            "postgres": postgres,
            "langfuse": "ok" if is_langfuse_enabled() else "disabled",
        },
    }
