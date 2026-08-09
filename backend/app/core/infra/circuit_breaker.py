"""断路器封装（基于 aiobreaker，原生 asyncio 支持）。

设计要点（2026 业界最佳实践，源自 Resilience4j / Microsoft Azure 文档）：
1. **aiobreaker 替代 pybreaker**：pybreaker 仅支持 Tornado 异步，不支持 async def。
2. **CircuitRedisStorage**：多 worker（uvicorn --workers N / gunicorn）下共享断路器状态，
   进程内 CircuitMemoryStorage 会导致每个 worker 各自计数、N 倍失败才熔断。
   Redis 不可用时降级为 CircuitMemoryStorage（开发期可用）。
3. **每个外部依赖独立断路器**：LLM(DeepSeek)/LLM(Ollama)/Embedding/Qdrant/Reranker/
   Redis/PostgreSQL 各自独立，避免一个组件失败影响其他组件的可用性判断。
4. **多 breaker 共用 Redis 时必须唯一 namespace**：否则状态会串。

注意：aiobreaker 原生不支持 success_threshold（Half-Open 探测次数限制），
Half-Open 行为由库内部处理（一次探测成功即 Closed，失败即 Open）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from aiobreaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitBreakerListener,
    CircuitBreakerState,
)
from aiobreaker.storage import CircuitMemoryStorage
from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# 导出 CircuitBreakerError 供调用方捕获（统一异常类型）
CircuitBreakerOpenError = CircuitBreakerError


def _make_storage(name: str, redis: Redis | None) -> Any:
    """创建断路器状态存储。

    优先 Redis（多 worker 共享）；Redis 不可用时降级为内存（开发期）。
    注意：Redis 存储不能用 decode_responses=True（aiobreaker 内部会 .decode()）。
    """
    if redis is None:
        logger.debug("[CB:%s] 使用 CircuitMemoryStorage（Redis 不可用）", name)
        return CircuitMemoryStorage(CircuitBreakerState.CLOSED)
    # aiobreaker CircuitRedisStorage 不兼容异步 redis（同步调 execute_command 返回 coroutine）
    # 降级内存存储；多 worker 共享需改用同步 redis 客户端或自定义异步 storage（TODO）
    logger.warning("[CB:%s] 异步 Redis 不兼容 CircuitRedisStorage，降级 CircuitMemoryStorage", name)
    return CircuitMemoryStorage(CircuitBreakerState.CLOSED)


_BREAKERS: dict[str, CircuitBreaker] = {}


class _StateChangeListener(CircuitBreakerListener):
    """断路器状态变更监听器（记录状态转换日志）。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def state_change(self, breaker: Any, old: Any, new: Any) -> None:
        logger.warning(
            "[CB:%s] 状态变更 %s -> %s",
            self.name,
            getattr(old, "name", old),
            getattr(new, "name", new),
        )


def init_breakers(redis: Redis | None = None) -> None:
    """初始化所有断路器。

    应在应用 lifespan 启动时调用，传入已连接的 Redis 客户端（不要带 decode_responses）。
    重复调用会重建断路器（用于测试）。
    """
    global _BREAKERS
    _BREAKERS = {
        "llm_deepseek": CircuitBreaker(
            fail_max=settings.CB_LLM_FAIL_THRESHOLD,
            timeout_duration=timedelta(seconds=settings.CB_LLM_OPEN_SECONDS),
            state_storage=_make_storage("llm_deepseek", redis),
            name="llm_deepseek",
        ),
        "llm_ollama": CircuitBreaker(
            fail_max=3,  # 备用 provider 更快熔断
            timeout_duration=timedelta(seconds=30),
            state_storage=_make_storage("llm_ollama", redis),
            name="llm_ollama",
        ),
        "embedding": CircuitBreaker(
            fail_max=settings.CB_EMBEDDING_FAIL_THRESHOLD,
            timeout_duration=timedelta(seconds=settings.CB_EMBEDDING_OPEN_SECONDS),
            state_storage=_make_storage("embedding", redis),
            name="embedding",
        ),
        "qdrant": CircuitBreaker(
            fail_max=settings.CB_QDRANT_FAIL_THRESHOLD,
            timeout_duration=timedelta(seconds=settings.CB_QDRANT_OPEN_SECONDS),
            state_storage=_make_storage("qdrant", redis),
            name="qdrant",
        ),
        "reranker": CircuitBreaker(
            fail_max=settings.CB_RERANKER_FAIL_THRESHOLD,
            timeout_duration=timedelta(seconds=60),
            state_storage=_make_storage("reranker", redis),
            name="reranker",
        ),
        "redis": CircuitBreaker(
            fail_max=settings.CB_REDIS_FAIL_THRESHOLD,
            timeout_duration=timedelta(seconds=settings.CB_REDIS_OPEN_SECONDS),
            state_storage=_make_storage("redis", redis),
            name="redis",
        ),
        "postgres": CircuitBreaker(
            fail_max=settings.CB_POSTGRES_FAIL_THRESHOLD,
            timeout_duration=timedelta(seconds=60),
            state_storage=_make_storage("postgres", redis),
            name="postgres",
        ),
        "prometheus": CircuitBreaker(
            fail_max=settings.CB_PROMETHEUS_FAIL_THRESHOLD,
            timeout_duration=timedelta(seconds=settings.CB_PROMETHEUS_OPEN_SECONDS),
            state_storage=_make_storage("prometheus", redis),
            name="prometheus",
        ),
        "elasticsearch": CircuitBreaker(
            fail_max=settings.CB_ELASTICSEARCH_FAIL_THRESHOLD,
            timeout_duration=timedelta(seconds=settings.CB_ELASTICSEARCH_OPEN_SECONDS),
            state_storage=_make_storage("elasticsearch", redis),
            name="elasticsearch",
        ),
        "alertmanager": CircuitBreaker(
            fail_max=settings.CB_ALERTMANAGER_FAIL_THRESHOLD,
            timeout_duration=timedelta(seconds=settings.CB_ALERTMANAGER_OPEN_SECONDS),
            state_storage=_make_storage("alertmanager", redis),
            name="alertmanager",
        ),
    }
    for name, breaker in _BREAKERS.items():
        breaker.add_listener(_StateChangeListener(name))
    logger.info("[CB] 已初始化 %d 个断路器（Redis=%s）", len(_BREAKERS), redis is not None)


def get_breaker(name: str) -> CircuitBreaker:
    """获取指定名称的断路器。

    若 init_breakers 未调用（如单元测试），自动用内存存储初始化。
    """
    if name not in _BREAKERS:
        logger.warning("[CB:%s] 断路器未初始化，自动用内存存储创建", name)
        init_breakers(redis=None)
    return _BREAKERS[name]


def is_open(name: str) -> bool:
    """断路器是否开启（Open 状态）。

    Half-Open 时不算完全 open（允许探测），调用方仍可尝试调用，
    但 aiobreaker 会自动限制。此处仅返回真正的 Open 状态，
    供调用方决策"是否完全跳过该 provider"。
    """
    breaker = get_breaker(name)
    return breaker.current_state == CircuitBreakerState.OPEN


async def call_with_breaker(
    name: str,
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """通过断路器调用 async 函数。

    - Closed: 正常调用
    - Open: 抛 CircuitBreakerError，调用方应捕获并走降级
    - Half-Open: aiobreaker 自动处理（一次探测成功即 Closed，失败即 Open）

    Raises:
        CircuitBreakerError: 断路器 Open 时抛出
    """
    breaker = get_breaker(name)
    # aiobreaker 的 call_async 原生支持 async 函数
    return await breaker.call_async(func, *args, **kwargs)
