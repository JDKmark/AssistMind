"""应用级 Prometheus 指标（T5）。

设计目标（对齐 Langfuse 旁路设计）：
1. **全程旁路**：任何一次指标写入都不得阻塞请求、不得抛异常、不得改变返回值与异常语义。
   所有 helper 内部 try/except，失败仅 ``logger.debug``（不刷 warning，避免采样抖动刷屏）。
2. **未启用即 no-op**：``METRICS_ENABLED=false`` 或 ``prometheus_client`` 未安装时，
   所有 helper 退化为空操作，``/metrics`` 仍能返回合法响应（空体），不报错。
3. **独立 registry**：使用专属 ``CollectorRegistry``，不污染 prometheus_client 默认
   registry，避免模块重复导入时 ``Duplicated timeseries`` 抛错。
4. **标签基数受控**：标签取值均为固定枚举（intent/stage/level/role/component/reason/
   provider/status/scope），禁止把 query / 用户名 / token 放进标签（高基数 + 信息泄漏）。

单进程语义：本模块使用默认（非 multiprocess）模式，指标仅在单进程内有效；多 worker
部署需改用 prometheus_client multiprocess 模式（见 docs/perf-baseline-*.md 说明）。

指标清单（命名 ``assistmind_*``，与 spec T5 一致）：
- assistmind_inflight_requests             Gauge      （在途请求数）
- assistmind_inflight_rejected_total       Counter    reason
- assistmind_request_duration_seconds      Histogram  intent
- assistmind_ttft_seconds                  Histogram  intent
- assistmind_stage_duration_seconds        Histogram  stage
- assistmind_cache_hit_total               Counter    level, role
- assistmind_degradation_total             Counter    component, reason
- assistmind_breaker_state                 Gauge      component
- assistmind_llm_upstream_errors_total     Counter    provider, status
- assistmind_rate_limited_total            Counter    scope
"""

from __future__ import annotations

import logging

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

try:  # pragma: no cover - 依赖缺失路径在 CI 有依赖时走不到
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover - 环境未装 prometheus_client
    _PROM_AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    CollectorRegistry = None  # type: ignore[assignment]


def _enabled() -> bool:
    """指标是否真正生效（依赖可用 + 配置开启）。"""
    return _PROM_AVAILABLE and bool(getattr(settings, "METRICS_ENABLED", True))


# ===== 指标定义（仅启用时创建；禁用/缺依赖时保持 None 走 no-op）=====
_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0)
_TTFT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0)
_STAGE_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0)

registry = None
inflight_requests = None
inflight_rejected_total = None
request_duration = None
ttft = None
stage_duration = None
cache_hit_total = None
degradation_total = None
breaker_state = None
llm_upstream_errors_total = None
rate_limited_total = None

if _enabled():
    try:
        registry = CollectorRegistry()
        inflight_requests = Gauge(
            "assistmind_inflight_requests",
            "当前在途请求数（SSE 长连接占用中）",
            registry=registry,
        )
        inflight_rejected_total = Counter(
            "assistmind_inflight_rejected_total",
            "因在途并发上限被拒绝的请求总数",
            labelnames=("reason",),
            registry=registry,
        )
        request_duration = Histogram(
            "assistmind_request_duration_seconds",
            "一轮请求端到端耗时（秒）",
            labelnames=("intent",),
            buckets=_DURATION_BUCKETS,
            registry=registry,
        )
        ttft = Histogram(
            "assistmind_ttft_seconds",
            "首个 delta 写出耗时（TTFT，秒）",
            labelnames=("intent",),
            buckets=_TTFT_BUCKETS,
            registry=registry,
        )
        stage_duration = Histogram(
            "assistmind_stage_duration_seconds",
            "各阶段耗时（秒）",
            labelnames=("stage",),
            buckets=_STAGE_BUCKETS,
            registry=registry,
        )
        cache_hit_total = Counter(
            "assistmind_cache_hit_total",
            "语义缓存命中/未命中计数",
            labelnames=("level", "role"),
            registry=registry,
        )
        degradation_total = Counter(
            "assistmind_degradation_total",
            "降级触发计数",
            labelnames=("component", "reason"),
            registry=registry,
        )
        breaker_state = Gauge(
            "assistmind_breaker_state",
            "断路器状态（0=closed 1=open 2=half_open）",
            labelnames=("component",),
            registry=registry,
        )
        llm_upstream_errors_total = Counter(
            "assistmind_llm_upstream_errors_total",
            "LLM 上游错误计数",
            labelnames=("provider", "status"),
            registry=registry,
        )
        rate_limited_total = Counter(
            "assistmind_rate_limited_total",
            "限流触发计数",
            labelnames=("scope",),
            registry=registry,
        )
    except Exception as e:  # pragma: no cover - 定义期异常降级为 no-op
        logger.warning("[Metrics] 指标注册失败，降级为 no-op: %s", e)
        registry = None
        _PROM_AVAILABLE = False


# ===== 对外 helper（全部旁路，永不抛异常）=====


def is_available() -> bool:
    """指标是否可用（/metrics 端点据此决定响应体）。"""
    return _enabled() and registry is not None


def safe(fn, *args, **kwargs) -> None:
    """在关键路径调用指标函数时的二次兜底（旁路硬要求）。

    metrics 各 helper 内部已自兜底；当调用点位于断路器状态变更、限流返回、
    降级分支等敏感位置时，再包一层确保「埋点异常永不改变控制流」。
    """
    try:
        fn(*args, **kwargs)
    except Exception as e:  # pragma: no cover - 指标异常不得影响主流程
        logger.debug("[Metrics] 指标写入失败（忽略）: %s", e)


def render() -> tuple[bytes, str]:
    """渲染当前指标快照，返回 (body, content_type)。

    不可用时返回空体 + 标准 content-type，避免 endpoint 500。
    """
    if not is_available():
        return b"# metrics disabled\n", CONTENT_TYPE_LATEST
    try:
        return generate_latest(registry), CONTENT_TYPE_LATEST
    except Exception as e:  # pragma: no cover
        logger.debug("[Metrics] 渲染失败: %s", e)
        return b"# metrics render error\n", CONTENT_TYPE_LATEST


def inc_inflight() -> None:
    _try(lambda: inflight_requests.inc())


def dec_inflight() -> None:
    _try(lambda: inflight_requests.dec())


def set_inflight(value: int) -> None:
    _try(lambda: inflight_requests.set(value))


def inc_inflight_rejected(reason: str) -> None:
    _try(lambda: inflight_rejected_total.labels(reason=reason).inc())


def observe_request(intent: str, seconds: float) -> None:
    _try(lambda: request_duration.labels(intent=intent or "unknown").observe(seconds))


def observe_ttft(intent: str, seconds: float) -> None:
    _try(lambda: ttft.labels(intent=intent or "unknown").observe(seconds))


def observe_stage(stage: str, seconds: float) -> None:
    _try(lambda: stage_duration.labels(stage=stage).observe(seconds))


def inc_cache_hit(level: str, role: str) -> None:
    _try(lambda: cache_hit_total.labels(level=level, role=role or "unknown").inc())


def inc_degradation(component: str, reason: str = "") -> None:
    _try(lambda: degradation_total.labels(component=component, reason=reason or "n/a").inc())


# 断路器状态映射（与 aiobreaker CircuitBreakerState 对应；避免直接依赖其枚举值）
_BREAKER_STATE_VALUE = {"closed": 0, "open": 1, "half_open": 2, "half-open": 2}


def set_breaker_state(component: str, state: str) -> None:
    value = _BREAKER_STATE_VALUE.get(str(state).lower(), 0)
    _try(lambda: breaker_state.labels(component=component).set(value))


def inc_llm_upstream_error(provider: str, status: str) -> None:
    _try(
        lambda: llm_upstream_errors_total.labels(
            provider=provider or "unknown", status=status or "error"
        ).inc()
    )


def inc_rate_limited(scope: str) -> None:
    _try(lambda: rate_limited_total.labels(scope=scope or "unknown").inc())


def _try(fn) -> None:
    """执行指标写入；任何异常都被吞掉（旁路性硬要求）。"""
    if not is_available():
        return
    try:
        fn()
    except Exception as e:  # pragma: no cover - 指标写入异常不得影响主流程
        logger.debug("[Metrics] 指标写入失败（忽略）: %s", e)
