"""LLM 工厂：provider 切换 + 指数退避重试 + 断路器 + 降级。

降级链路（修正版，P0 bug 已修复）：
1. 主 provider（DeepSeek）调用，30s 超时
2. 可重试错误（429/5xx/超时）→ 指数退避重试（base=1s, jitter=0.5s）
3. 不可重试错误（4xx 非 429）→ 不重试，直接进入降级
4. 主 provider 断路器 Open 或重试失败 → 切备用 provider（Ollama，15s 超时）
5. 备用也失败 → 抛出 LLMUnavailableError，由调用方决定场景化降级

P0 修复说明：
原实现中 _call_with_retry 定义但从未被调用，timeout/max_retries 通过 **kwargs 被忽略。
现用 tenacity 显式装饰 _call_*_via_breaker，确保每次重试独立经过断路器计数。

Langfuse 埋点（phase5 Task 2）：
- 每次 call_llm 产生一个 span（name=llm.call），metadata 记录 provider/model/超时/重试次数/是否降级，
  output 记录生成内容，metadata 同时记录耗时(ms)与内容长度；异常时 span 记 ERROR 后原样抛出。
- 未启用（is_langfuse_enabled() False）时零开销：不构造 span、不产生任何额外调用，
  返回值与异常语义与未埋点时完全一致。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import openai
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langfuse import LangfuseSpan
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import get_settings
from app.core.infra import fault_injection, metrics
from app.core.infra.circuit_breaker import (
    CircuitBreakerOpenError,
    call_with_breaker,
    is_open,
)
from app.core.infra.langfuse import get_langfuse, is_langfuse_enabled

logger = logging.getLogger(__name__)
settings = get_settings()


def _error_status(exc: BaseException) -> str:
    """把异常归类为低基数状态标签（provider/status 指标用，禁止把异常原文放标签）。"""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(exc, openai.RateLimitError):
        return "429"
    if isinstance(exc, openai.InternalServerError):
        return "5xx"
    return "error"


# ===== Mock LLM（LLM_PROVIDER=mock，spec 3.5 离线压测）=====
# 按 prompt 形态返回确定性内容，使全链路（改写 / 意图 / CRAG / 生成）都能在不触达
# 上游的前提下走通；生成阶段按固定 token 数与固定间隔流式返回，用于测纯服务端能力。
def _mock_payload(prompt: str) -> str:
    """按 prompt 语义返回 mock 文本（辅助环节返回结构化小响应，生成阶段返回长文本）。"""
    p = prompt or ""
    # CRAG 相关性评分：返回可直接解析的分数
    if "相关性得分" in p or "得分：" in p:
        return "1.0"
    # 意图分类：返回合法 JSON
    if "意图分类器" in p or "分类 JSON" in p:
        return '{"intent": "faq", "confidence": 0.95}'
    # 查询改写：返回 N 个变体（每行一个）
    if "查询改写" in p and "变体" in p:
        n = max(1, settings.QUERY_REWRITE_NUM_VARIANTS)
        seed = p.strip().splitlines()[0][:24] if p.strip() else "问题"
        return "\n".join(f"变体{i + 1}：{seed}" for i in range(n))
    # 生成阶段：固定长度文本（token 近似按字符计）
    base = settings.MOCK_LLM_ANSWER or (
        "这是 AssistMind 智能客服的模拟回答，用于离线压测隔离上游模型，"
        "以便测量服务端并发槽、连接池、检索重排与 SSE 写出的真实能力。"
    )
    tokens = max(1, settings.MOCK_LLM_TOKENS)
    text = (base * (tokens // max(1, len(base)) + 1))[:tokens]
    return text


async def _mock_stream(prompt: str, *, info: dict[str, Any] | None = None) -> AsyncIterator[str]:
    """Mock 流式生成：固定 token 数 × 固定间隔（默认 300 × 20ms ≈ 6s）。"""
    if info is not None:
        info["provider"] = "mock"
        info["model"] = "mock-llm"
        info["timeout_s"] = 0
    payload = _mock_payload(prompt)
    interval = max(0, settings.MOCK_LLM_INTERVAL_MS) / 1000.0
    for ch in payload:
        yield ch
        if interval:
            await asyncio.sleep(interval)


def _safe_span_update(span: LangfuseSpan, **kwargs: Any) -> None:
    """安全更新 Langfuse span。

    埋点是旁路逻辑：更新失败只记日志，绝不能抛异常影响 LLM 调用主流程。
    """
    try:
        span.update(**kwargs)
    except Exception as e:
        logger.warning("[Langfuse] span 更新失败（忽略，不影响调用）: %s", e)


def _safe_span_end(span: LangfuseSpan) -> None:
    """安全结束 Langfuse span（同上，结束时也必须兜底，不能影响主流程）。"""
    try:
        span.end()
    except Exception as e:
        logger.warning("[Langfuse] span 结束失败（忽略，不影响调用）: %s", e)


class LLMUnavailableError(Exception):
    """所有 LLM provider 均不可用。调用方应走场景化降级。"""


# 可重试异常类型（429/5xx/超时/连接错误）
# 4xx 非 429 不在列表中，tenacity 不会重试
_RETRYABLE_EXC: tuple[type[Exception], ...] = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
    TimeoutError,
    asyncio.TimeoutError,
)


def _build_retry_decorator(*, max_retries: int, base_delay: float, jitter: float):
    """构造 tenacity 重试装饰器。

    Retry 在外层，Breaker 在内层：每次重试独立经过断路器计数，
    避免单次请求 N 次失败被断路器计入 N 次（应只计入 1 次"最终失败"）。

    但 tenacity 的 retry 是装饰整个函数，函数内部调用 breaker.call()，
    所以一次"逻辑请求"内最多 N+1 次调用 breaker.call()，每次都计数。
    这是符合预期的：每次重试都向 breaker 报告状态，breaker 据此判断服务健康度。
    """
    return retry(
        stop=stop_after_attempt(max_retries + 1),
        wait=wait_exponential_jitter(initial=base_delay, jitter=jitter),
        retry=retry_if_exception_type(_RETRYABLE_EXC + (CircuitBreakerOpenError,)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


# DeepSeek 重试装饰器（外层 Retry，内层 Breaker）
_deepseek_retry = _build_retry_decorator(
    max_retries=settings.LLM_MAX_RETRIES,
    base_delay=settings.LLM_RETRY_BASE_DELAY,
    jitter=settings.LLM_RETRY_JITTER,
)

# Ollama 重试装饰器（备用 provider，重试次数少）
_ollama_retry = _build_retry_decorator(
    max_retries=1,
    base_delay=settings.LLM_RETRY_BASE_DELAY,
    jitter=settings.LLM_RETRY_JITTER,
)


def _build_chat_model(name: str, *, stream_fast: bool = False) -> ChatOpenAI:
    """按 provider 名构造 ChatOpenAI（供核心调用与流式共用）。

    stream_fast=True 时使用流式通道专属快速超时（LLM_STREAM_TIMEOUT /
    LLM_STREAM_FALLBACK_TIMEOUT）——生成阶段失败兜底只需 ~16s，而非
    LLM_TIMEOUT+重试+Ollama 15s ≈ 90s。
    """
    if name == "ollama":
        timeout = (
            settings.LLM_STREAM_FALLBACK_TIMEOUT
            if stream_fast
            else settings.LLM_FALLBACK_TIMEOUT
        )
        return ChatOpenAI(
            model=settings.OLLAMA_MODEL,
            api_key="ollama",
            base_url=settings.OLLAMA_BASE_URL + "/v1",
            temperature=0.3,
            max_tokens=2048,
            timeout=timeout,
        )
    timeout = settings.LLM_STREAM_TIMEOUT if stream_fast else settings.LLM_TIMEOUT
    return ChatOpenAI(
        model=settings.DEEPSEEK_MODEL,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL,
        temperature=0.3,
        max_tokens=2048,
        timeout=timeout,
    )


def _build_messages(prompt: str, system: str | None) -> list[Any]:
    messages: list[Any] = []
    if system:
        messages.append(("system", system))
    messages.append(("human", prompt))
    return messages


async def _astream_collect(llm: ChatOpenAI, messages: list[Any]) -> str:
    """用 astream 收集完整响应文本。

    关键：商汤网关对非流式整答的 TTFT 很高（实测 ~19s），而流式 TTFT 仅 ~2s。
    内部流式收集可让所有 LLM 调用提速一个数量级，且返回值契约（str）不变。
    """
    parts: list[str] = []
    async for chunk in llm.astream(messages):
        content = getattr(chunk, "content", "") or ""
        if content:
            parts.append(content)
    return "".join(parts)


async def _call_deepseek_core(
    prompt: str, system: str | None, *, info: dict[str, Any] | None = None
) -> str:
    """DeepSeek 核心调用（不含重试和断路器）。

    info: Langfuse 埋点统计容器，每次真实尝试（含重试）attempts +1；
        None 表示未启用埋点，本函数零额外开销。
    """
    if info is not None:
        info["deepseek_attempts"] += 1
    # 故障注入（T6）：在 provider core 层抛出，交由既有重试/断路器/降级链处理
    await fault_injection.apply_llm_fault("deepseek")
    llm = _build_chat_model("deepseek")
    return await _astream_collect(llm, _build_messages(prompt, system))


async def _call_ollama_core(
    prompt: str, system: str | None, *, info: dict[str, Any] | None = None
) -> str:
    """Ollama 核心调用（不含重试和断路器）。

    info: 同 _call_deepseek_core，统计 ollama 尝试次数。
    """
    if info is not None:
        info["ollama_attempts"] += 1
    # 故障注入（T6，同 DeepSeek 路径）
    await fault_injection.apply_llm_fault("ollama")
    llm = _build_chat_model("ollama")
    return await _astream_collect(llm, _build_messages(prompt, system))


# 通过断路器调用的版本（被 tenacity 重试包裹）
async def _call_deepseek_via_breaker(
    prompt: str, system: str | None, *, info: dict[str, Any] | None = None
) -> str:
    return await call_with_breaker(
        "llm_deepseek", _call_deepseek_core, prompt, system, info=info
    )


async def _call_ollama_via_breaker(
    prompt: str, system: str | None, *, info: dict[str, Any] | None = None
) -> str:
    return await call_with_breaker(
        "llm_ollama", _call_ollama_core, prompt, system, info=info
    )


# 应用重试装饰器（tenacity 对 async 函数原生支持）
_deepseek_with_retry = _deepseek_retry(_call_deepseek_via_breaker)
_ollama_with_retry = _ollama_retry(_call_ollama_via_breaker)


async def _call_llm_impl(
    prompt: str,
    system: str | None = None,
    *,
    generation: bool = False,
    info: dict[str, Any] | None = None,
    fast: bool = False,
) -> str:
    """原 call_llm 主体：降级链路 DeepSeek（重试+断路器）→ Ollama（重试+断路器）→ 异常。

    info: Langfuse 埋点统计容器，None 时行为与改动前完全一致（零额外开销）。
        核心函数在每次真实尝试时递增 attempts；本函数在成败时填写 provider/model/超时与降级标记。

    fast=True 供「失败可降级」的辅助环节使用（查询改写 / CRAG 评估 / 意图分类等）：
    直接走流式快速链路（LLM_STREAM_TIMEOUT=10s → Ollama 6s，无重试），失败感知 ~16s，
    避免这些环节在商汤故障时各自等待 90s+（曾让 faq 一轮感知数分钟）。
    """
    if fast:
        parts: list[str] = []
        async for d in _stream_impl(prompt, system, info=info):
            parts.append(d)
        return "".join(parts)

    # Mock provider（LLM_PROVIDER=mock）：不触达上游，直接返回确定性内容
    if settings.LLM_PROVIDER == "mock":
        parts_m: list[str] = []
        try:
            async for d in _stream_provider("mock", prompt, system, info=info):
                parts_m.append(d)
        except LLMUnavailableError:
            raise
        except Exception as e:
            # 与流式路径一致：mock 单 provider 失败 → 收敛为不可用，走调用方降级
            raise LLMUnavailableError("所有 LLM provider 均不可用（mock）") from e
        return "".join(parts_m)

    # 显式配置主 provider=ollama（如无预算用本地模型）：跳过 DeepSeek（含 429 重试浪费）
    if settings.LLM_PROVIDER == "ollama":
        if info is not None:
            info["fallback"] = True
        logger.info("[LLM] LLM_PROVIDER=ollama，直接使用本地 Ollama")

    # 主 provider：断路器 Open 时跳过，避免无谓重试
    elif not is_open("llm_deepseek"):
        try:
            result = await _deepseek_with_retry(prompt, system, info=info)
            if info is not None:
                info["provider"] = "deepseek"
                info["model"] = settings.DEEPSEEK_MODEL
                info["timeout_s"] = settings.LLM_TIMEOUT
            return result
        except CircuitBreakerOpenError:
            if info is not None:
                info["fallback"] = True
            metrics.safe(metrics.inc_llm_upstream_error, "deepseek", "breaker_open")
            logger.warning("[LLM] DeepSeek 断路器 Open，跳过直接切 Ollama")
        except Exception as e:
            if info is not None:
                info["fallback"] = True
            metrics.safe(metrics.inc_llm_upstream_error, "deepseek", _error_status(e))
            # 检查是否是断路器刚刚 Open（重试过程中触发的）
            if is_open("llm_deepseek"):
                logger.warning("[LLM] DeepSeek 重试中触发断路器 Open，切 Ollama: %s", e)
            else:
                logger.warning("[LLM] DeepSeek 重试耗尽，切 Ollama: %s", e)
    else:
        if info is not None:
            info["fallback"] = True
        logger.info("[LLM] DeepSeek 断路器 Open，直接使用 Ollama")

    # 备用 provider
    if not is_open("llm_ollama"):
        try:
            result = await _ollama_with_retry(prompt, system, info=info)
            if info is not None:
                info["provider"] = "ollama"
                info["model"] = settings.OLLAMA_MODEL
                info["timeout_s"] = settings.LLM_FALLBACK_TIMEOUT
            return result
        except CircuitBreakerOpenError:
            metrics.safe(metrics.inc_llm_upstream_error, "ollama", "breaker_open")
            logger.warning("[LLM] Ollama 断路器 Open")
        except Exception as e:
            metrics.safe(metrics.inc_llm_upstream_error, "ollama", _error_status(e))
            if is_open("llm_ollama"):
                logger.warning("[LLM] Ollama 重试中触发断路器 Open: %s", e)
            else:
                logger.warning("[LLM] Ollama 重试耗尽: %s", e)
    else:
        logger.warning("[LLM] Ollama 断路器也 Open，无可用 provider")

    raise LLMUnavailableError("所有 LLM provider 均不可用")


async def call_llm(
    prompt: str, system: str | None = None, *, generation: bool = False, fast: bool = False
) -> str:
    """调用 LLM 生成文本。

    Args:
        prompt: 用户提示
        system: 系统提示（可选）
        generation: 生成场景标记（兼容保留：当前两分支行为一致，均抛
            LLMUnavailableError 由调用方场景化降级；仅用于 Langfuse 埋点区分）
        fast: 快速失败模式（供 rewrite/CRAG/意图分类等「失败可降级」环节）。
            流式快速超时（10s→Ollama 6s，无重试），失败感知 ~16s；
            正常路径行为与未启用时完全一致。

    降级链路：DeepSeek（重试+断路器）→ Ollama（重试+断路器）→ LLMUnavailableError

    Raises:
        LLMUnavailableError: 所有 provider 均不可用

    Langfuse 埋点（每次调用产生一个 span，name=llm.call）：
    - metadata 含 provider（实际成功 provider）、model、超时、各 provider 尝试/重试次数、是否降级；
    - output 记录生成内容，metadata 记录耗时(ms) 与内容长度；异常时 span 记 ERROR 后原样抛出。
    - 未启用 Langfuse 时零开销直通 _call_llm_impl，行为与未埋点时完全一致。
    """
    if not is_langfuse_enabled():
        # 未启用：不构造 span、不产生任何额外调用，走与改动前完全一致的路径
        return await _call_llm_impl(prompt, system, generation=generation, fast=fast)

    client = get_langfuse()
    if client is None:
        # 防御性兜底（enabled 时理论上不会走到这里）：降级为不埋点
        return await _call_llm_impl(prompt, system, generation=generation, fast=fast)

    # ---- 埋点路径：一次 call_llm 对应一个 span，包住整个降级/重试链路 ----
    # 实现方式说明（langfuse 4.14 源码确认）：
    # - 4.14 已将 start_span/start_trace 统一为 client.start_observation(as_type="span")，
    #   返回的 span 提供 update()/end()，生命周期由我们手动控制（async 路径必须包住整个 await）。
    # - 不用 @observe() + langfuse_context：装饰器在函数定义时静态包装，未启用时无法真正
    #   "零开销"跳过；且这里需要按 provider 分派、把重试/降级信息动态写入同一个 span，
    #   手动持有 span 引用最直接。
    # - 嵌套继承：start_observation 底层是 OTEL tracer.start_span()，会自动以当前 context
    #   中活跃的 span（由外层 @observe()/start_as_current_observation 设置）为父——外层有
    #   trace 时本 span 自动挂接为子 span；无外层时自成新 trace 根。无需 langfuse_context。
    try:
        span = client.start_observation(
            name="llm.call",
            as_type="span",
            input={"prompt": prompt, "system": system, "generation": generation},
            metadata={
                "provider": settings.LLM_PROVIDER,
                "model": settings.DEEPSEEK_MODEL,
                "timeout_s": settings.LLM_TIMEOUT,
            },
        )
    except Exception as e:
        # 埋点初始化失败不能影响主流程：记日志后走不埋点路径
        logger.warning("[Langfuse] span 创建失败，本次调用不埋点: %s", e)
        return await _call_llm_impl(prompt, system, generation=generation)

    # 统计容器：核心函数在每次真实尝试（含重试）时递增，impl 在成败时填写 provider 信息
    info: dict[str, Any] = {
        "provider": None,
        "model": None,
        "timeout_s": None,
        "deepseek_attempts": 0,
        "ollama_attempts": 0,
        "fallback": False,
    }
    start = time.perf_counter()

    def _final_metadata(status: str, duration_ms: int, **extra: Any) -> dict[str, Any]:
        """汇总最终 metadata：provider/模型/超时/各 provider 尝试与重试次数/是否降级/耗时。"""
        return {
            "provider": info["provider"] or "none",
            "model": info["model"],
            "timeout_s": info["timeout_s"],
            "deepseek_attempts": info["deepseek_attempts"],
            "ollama_attempts": info["ollama_attempts"],
            "retry_count": max(
                0, info["deepseek_attempts"] + info["ollama_attempts"] - 1
            ),
            "fallback": info["fallback"],
            "status": status,
            "duration_ms": duration_ms,
            **extra,
        }

    try:
        result = await _call_llm_impl(
            prompt, system, generation=generation, info=info, fast=fast
        )
    except BaseException as exc:
        # 异常路径：span 记 ERROR 后仍须 end，然后原样抛出（不改变异常语义与断路器行为）
        _safe_span_update(
            span,
            level="ERROR",
            status_message=f"{type(exc).__name__}: {exc}",
            metadata=_final_metadata("error", int((time.perf_counter() - start) * 1000)),
        )
        raise
    else:
        # 成功路径：output 记录生成内容，metadata 记录耗时(ms) 与内容长度
        _safe_span_update(
            span,
            output=result,
            metadata=_final_metadata(
                "ok",
                int((time.perf_counter() - start) * 1000),
                content_length=len(result),
            ),
        )
        return result
    finally:
        # 无论成败都必须结束 span（end 后 span 不可再修改）
        _safe_span_end(span)


async def _stream_provider(
    name: str, prompt: str, system: str | None, *, info: dict[str, Any] | None = None
) -> AsyncIterator[str]:
    """单个 provider 的流式生成（逐 chunk yield 文本片段）。

    语义：首 token 之前失败 → 原样上抛（上层据此降级到下一 provider）；
    已开始产出后再失败 → 记 warning 后同样上抛（SSE 语义：流已开始不重试/不切换）。
    """
    if name == "mock":
        # Mock provider：不触达上游，固定 token 数与间隔流式返回（离线压测）
        await fault_injection.apply_llm_fault("mock")
        async for chunk in _mock_stream(prompt, info=info):
            yield chunk
        return
    # 故障注入（T6）：首 token 前抛出，交由上层既有降级链切换到下一 provider
    await fault_injection.apply_llm_fault(name)
    if name == "deepseek":
        if info is not None:
            info["deepseek_attempts"] += 1
    else:
        if info is not None:
            info["ollama_attempts"] += 1
    llm = _build_chat_model(name, stream_fast=True)
    messages = _build_messages(prompt, system)
    started = False
    try:
        async for chunk in llm.astream(messages):
            content = getattr(chunk, "content", "") or ""
            if not content:
                continue
            started = True
            yield content
    except Exception as e:
        if started:
            logger.warning("[LLM] %s 流式中途失败: %s", name, e)
        raise


async def _stream_impl(
    prompt: str, system: str | None, *, info: dict[str, Any] | None = None
) -> AsyncIterator[str]:
    """stream_llm 降级链：DeepSeek 流式 → Ollama 流式 → LLMUnavailableError。

    断路器只做「开始前检查」（流式无法安全地在已产出后重试/切换）；
    provider 首 token 前失败时降级到下一 provider；已产出后失败原样上抛——
    不切 provider（避免半截话与全文拼接成重复内容）、不转 LLMUnavailableError
    （那会让下游把 done 覆盖成兜底话术，与已推送的 delta 不一致），由 SSE 层
    以 error 事件终止流。
    """
    # Mock provider：单 provider 直通，不触达上游
    if settings.LLM_PROVIDER == "mock":
        produced = False
        try:
            async for d in _stream_provider("mock", prompt, system, info=info):
                produced = True
                yield d
        except Exception as e:
            # mock 模式下没有备用 provider：首 token 前失败统一收敛为"不可用"，
            # 使调用方走既有的场景化降级（模板兜底/降级话术），保持降级语义一致。
            if produced:
                raise
            raise LLMUnavailableError("所有 LLM provider 均不可用（流式 mock）") from e
        return

    if settings.LLM_PROVIDER == "ollama":
        if is_open("llm_ollama"):
            if info is not None:
                info["fallback"] = True
            metrics.safe(metrics.inc_llm_upstream_error, "ollama", "breaker_open")
            logger.warning("[LLM] Ollama 断路器 Open（流式，单 provider 模式）")
            raise LLMUnavailableError("所有 LLM provider 均不可用（流式）")
        produced = False
        try:
            async for d in _stream_provider("ollama", prompt, system, info=info):
                produced = True
                yield d
        except Exception as e:
            if not produced:
                metrics.safe(metrics.inc_llm_upstream_error, "ollama", _error_status(e))
                raise LLMUnavailableError("所有 LLM provider 均不可用（流式）") from e
            raise
        if info is not None:
            info["provider"] = "ollama"
            info["model"] = settings.OLLAMA_MODEL
            info["timeout_s"] = settings.LLM_STREAM_FALLBACK_TIMEOUT
        return

    if not is_open("llm_deepseek"):
        produced = False
        try:
            async for d in _stream_provider("deepseek", prompt, system, info=info):
                produced = True
                yield d
            if info is not None:
                info["provider"] = "deepseek"
                info["model"] = settings.DEEPSEEK_MODEL
                info["timeout_s"] = settings.LLM_STREAM_TIMEOUT
            return
        except Exception as e:
            if produced:
                # 已产出后失败：原样上抛，切 Ollama 会把半截话与全文拼在一起
                raise
            if info is not None:
                info["fallback"] = True
            metrics.safe(metrics.inc_llm_upstream_error, "deepseek", _error_status(e))
            logger.warning("[LLM] DeepSeek 流式失败，切 Ollama: %s", e)
    else:
        if info is not None:
            info["fallback"] = True
        metrics.safe(metrics.inc_llm_upstream_error, "deepseek", "breaker_open")
        logger.warning("[LLM] DeepSeek 断路器 Open（流式），直接 Ollama")

    if not is_open("llm_ollama"):
        produced = False
        try:
            async for d in _stream_provider("ollama", prompt, system, info=info):
                produced = True
                yield d
            if info is not None:
                info["provider"] = "ollama"
                info["model"] = settings.OLLAMA_MODEL
                info["timeout_s"] = settings.LLM_STREAM_FALLBACK_TIMEOUT
            return
        except Exception as e:
            if produced:
                # 最后一个 provider 已产出后失败：原样上抛（SSE error 终止），
                # 不转 LLMUnavailableError——done 兜底话术会覆盖已发 delta
                raise
            metrics.safe(metrics.inc_llm_upstream_error, "ollama", _error_status(e))
            if is_open("llm_ollama"):
                logger.warning("[LLM] Ollama 流式触发断路器 Open: %s", e)
            else:
                logger.warning("[LLM] Ollama 流式失败: %s", e)
    else:
        metrics.safe(metrics.inc_llm_upstream_error, "ollama", "breaker_open")
        logger.warning("[LLM] Ollama 断路器也 Open（流式），无可用 provider")

    raise LLMUnavailableError("所有 LLM provider 均不可用（流式）")


async def stream_llm(
    prompt: str, system: str | None = None, *, generation: bool = False
) -> AsyncIterator[str]:
    """流式调用 LLM：逐 chunk yield 文本片段（SSE 打字机效果用）。

    Args:
        prompt: 用户提示
        system: 系统提示（可选）
        generation: 兼容保留（与 call_llm 同语义，仅用于 Langfuse 埋点区分）

    降级链路：DeepSeek（流式，首 token 前失败切备）→ Ollama（流式）→ LLMUnavailableError。
    与 call_llm 的区别：逐 token 产出（TTFT 快），不在中途重试/切换 provider。

    Langfuse 埋点（一次流式 = 一个 span，name=llm.stream）：
    正常消费完在 output 记完整文本 + metadata（provider/耗时/长度）；异常记 ERROR。
    未启用 Langfuse 时零开销直通 _stream_impl。
    """
    if not is_langfuse_enabled():
        async for d in _stream_impl(prompt, system):
            yield d
        return

    client = get_langfuse()
    if client is None:
        async for d in _stream_impl(prompt, system):
            yield d
        return

    try:
        span = client.start_observation(
            name="llm.stream",
            as_type="span",
            input={"prompt": prompt, "system": system, "generation": generation},
            metadata={
                "provider": settings.LLM_PROVIDER,
                "model": settings.DEEPSEEK_MODEL,
                "timeout_s": settings.LLM_STREAM_TIMEOUT,
            },
        )
    except Exception as e:
        logger.warning("[Langfuse] span 创建失败，本次流式不埋点: %s", e)
        async for d in _stream_impl(prompt, system):
            yield d
        return

    info: dict[str, Any] = {
        "provider": None,
        "model": None,
        "timeout_s": None,
        "deepseek_attempts": 0,
        "ollama_attempts": 0,
        "fallback": False,
    }
    start = time.perf_counter()
    parts: list[str] = []
    try:
        async for d in _stream_impl(prompt, system, info=info):
            parts.append(d)
            yield d
    except BaseException as exc:
        _safe_span_update(
            span,
            level="ERROR",
            status_message=f"{type(exc).__name__}: {exc}",
            metadata={
                "provider": info["provider"] or "none",
                "model": info["model"],
                "fallback": info["fallback"],
                "timeout_s": info["timeout_s"],
                "status": "error",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            },
        )
        raise
    else:
        result = "".join(parts)
        _safe_span_update(
            span,
            output=result,
            metadata={
                "provider": info["provider"] or "none",
                "model": info["model"],
                "fallback": info["fallback"],
                "timeout_s": info["timeout_s"],
                "status": "ok",
                "duration_ms": int((time.perf_counter() - start) * 1000),
                "content_length": len(result),
            },
        )
    finally:
        _safe_span_end(span)


def get_chat_model() -> BaseChatModel:
    """获取 LangChain ChatModel 实例（用于 LangGraph Agent）。

    优先 DeepSeek，断路器 Open 时切 Ollama。
    注意：LangChain ChatModel 自带重试，这里不做双重重试，仅做 provider 切换。
    """
    # 显式配置主 provider 为 ollama 时，直接返回 Ollama（避免空 api_key 构造 DeepSeek）
    if settings.LLM_PROVIDER == "ollama":
        return ChatOpenAI(
            model=settings.OLLAMA_MODEL,
            api_key="ollama",
            base_url=settings.OLLAMA_BASE_URL + "/v1",
            temperature=0.3,
            timeout=settings.LLM_FALLBACK_TIMEOUT,
        )
    # mock 模式：当前 Agent 走 call_llm（非本函数），此处仅保证构造不因空 key 崩溃
    if settings.LLM_PROVIDER == "mock":
        return ChatOpenAI(
            model="mock-llm",
            api_key="mock",
            base_url=settings.OLLAMA_BASE_URL + "/v1",
            temperature=0.3,
            timeout=settings.LLM_FALLBACK_TIMEOUT,
        )
    if not is_open("llm_deepseek"):
        return ChatOpenAI(
            model=settings.DEEPSEEK_MODEL,
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            temperature=0.3,
            timeout=settings.LLM_TIMEOUT,
        )
    return ChatOpenAI(
        model=settings.OLLAMA_MODEL,
        api_key="ollama",
        base_url=settings.OLLAMA_BASE_URL + "/v1",
        temperature=0.3,
        timeout=settings.LLM_FALLBACK_TIMEOUT,
    )
