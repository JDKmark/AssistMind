"""断路器单元测试。

覆盖：
- 初始化 7 个独立断路器
- is_open 正确反映状态
- call_with_breaker 成功/失败计数
- 连续失败 N 次后 Open
"""

from __future__ import annotations

import pytest
from aiobreaker import CircuitBreakerError

from app.core.infra import circuit_breaker
from app.core.infra.circuit_breaker import (
    call_with_breaker,
    get_breaker,
    init_breakers,
    is_open,
)


def test_init_breakers_creates_all():
    """初始化后应创建 7 个独立断路器。"""
    init_breakers(redis=None)
    expected = {"llm_deepseek", "llm_ollama", "embedding", "qdrant", "reranker", "redis", "postgres"}
    assert expected.issubset(set(circuit_breaker._BREAKERS.keys()))


def test_is_open_initially_false():
    """新建断路器初始状态应为 Closed（is_open=False）。"""
    init_breakers(redis=None)
    assert not is_open("llm_deepseek")
    assert not is_open("qdrant")


async def test_call_with_breaker_success():
    """成功调用不应改变断路器状态。"""
    init_breakers(redis=None)

    async def _success():
        return "ok"

    result = await call_with_breaker("llm_deepseek", _success)
    assert result == "ok"
    assert not is_open("llm_deepseek")


async def test_call_with_breaker_failure_counts():
    """失败调用应被断路器计数。"""
    init_breakers(redis=None)

    async def _fail():
        raise ValueError("服务失败")

    with pytest.raises(ValueError):
        await call_with_breaker("llm_ollama", _fail)
    # Ollama 备用 provider fail_max=3，一次失败不应 Open
    assert not is_open("llm_ollama")


async def test_breaker_opens_after_fail_threshold():
    """连续失败达阈值后断路器应 Open。

    注意：aiobreaker 在第 fail_max 次失败时直接抛 CircuitBreakerError（而非原异常），
    表示断路器已打开。前 fail_max-1 次抛原异常。
    """
    init_breakers(redis=None)

    # Ollama fail_max=3
    async def _fail():
        raise ConnectionError("服务挂了")

    # 前 2 次抛原异常 ConnectionError
    for _ in range(2):
        with pytest.raises(ConnectionError):
            await call_with_breaker("llm_ollama", _fail)

    # 第 3 次达阈值，抛 CircuitBreakerError（断路器打开）
    with pytest.raises(CircuitBreakerError):
        await call_with_breaker("llm_ollama", _fail)

    # 此后断路器 Open，直接抛 CircuitBreakerError
    assert is_open("llm_ollama")
    with pytest.raises(CircuitBreakerError):
        await call_with_breaker("llm_ollama", _fail)


async def test_get_breaker_auto_init():
    """未初始化时调用 get_breaker 应自动初始化（内存存储）。"""
    # 清空后访问
    circuit_breaker._BREAKERS = {}
    breaker = get_breaker("llm_deepseek")
    assert breaker is not None
    assert "llm_deepseek" in circuit_breaker._BREAKERS
