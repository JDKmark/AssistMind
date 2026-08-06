"""Prometheus 客户端：Range Query / Label Values 查询 + 失败降级。

对接 Prometheus HTTP API（/api/v1/query_range、/api/v1/label/{label}/values）。
失败降级：
- 未配置 PROMETHEUS_URL：is_connected=False，调用方（real 数据源）返回空数据
- 查询失败：通过断路器计数，返回空列表
- 断路器 Open：直接返回空列表，避免无谓调用
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.core.infra.circuit_breaker import (
    CircuitBreakerOpenError,
    call_with_breaker,
    is_open,
)

logger = logging.getLogger(__name__)
settings = get_settings()


class PrometheusClient:
    """Prometheus HTTP API 异步客户端。"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        if not settings.PROMETHEUS_URL:
            logger.warning("[Prometheus] 未配置 PROMETHEUS_URL，数据源不可用")
            self._client = None
            return
        try:
            self._client = httpx.AsyncClient(
                base_url=settings.PROMETHEUS_URL,
                timeout=settings.PROMETHEUS_TIMEOUT,
            )
            logger.info("[Prometheus] 连接就绪，base_url=%s", settings.PROMETHEUS_URL)
        except Exception as e:
            self._client = None
            logger.warning("[Prometheus] 客户端创建失败: %s", e)

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    async def ping(self) -> bool:
        """健康探测（/-/healthy）。一次性调用，不参与断路器，失败仅返回 False。"""
        if not self._client:
            return False
        try:
            resp = await self._client.get("/-/healthy", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    async def query_range(
        self,
        expr: str,
        start_ts: int,
        end_ts: int,
        step: int = 60,
    ) -> list[dict[str, Any]]:
        """Range Query，返回 [{ts, value}]。

        多序列结果只取第一个（表达式应保证单序列）；
        status != success 或 result 为空返回 []。
        """
        if not self._client:
            return []
        if is_open("prometheus"):
            logger.warning("[Prometheus] 断路器 Open，跳过 query_range")
            return []
        try:
            async def _do_query() -> list[dict[str, Any]]:
                resp = await self._client.get(
                    "/api/v1/query_range",
                    params={
                        "query": expr,
                        "start": start_ts,
                        "end": end_ts,
                        "step": step,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "success":
                    logger.warning("[Prometheus] query_range 返回异常状态: %s", data.get("status"))
                    return []
                results = data.get("data", {}).get("result", [])
                if not results:
                    return []
                values = results[0].get("values", [])
                return [
                    {"ts": int(ts), "value": float(value)}
                    for ts, value in values
                    if value not in (None, "", "NaN")
                ]

            return await call_with_breaker("prometheus", _do_query)
        except CircuitBreakerOpenError:
            logger.warning("[Prometheus] query_range 断路器 Open")
            return []
        except Exception as e:
            logger.warning("[Prometheus] query_range 失败: %s", e)
            return []

    async def label_values(self, label: str) -> list[str]:
        """查询 label 的全部取值（用于服务/实例列表）。"""
        if not self._client:
            return []
        if is_open("prometheus"):
            logger.warning("[Prometheus] 断路器 Open，跳过 label_values")
            return []
        try:
            async def _do_label() -> list[str]:
                resp = await self._client.get(f"/api/v1/label/{label}/values")
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "success":
                    logger.warning("[Prometheus] label_values 返回异常状态: %s", data.get("status"))
                    return []
                return [str(v) for v in data.get("data", [])]

            return await call_with_breaker("prometheus", _do_label)
        except CircuitBreakerOpenError:
            logger.warning("[Prometheus] label_values 断路器 Open")
            return []
        except Exception as e:
            logger.warning("[Prometheus] label_values 失败: %s", e)
            return []

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


_prometheus: PrometheusClient | None = None


def get_prometheus() -> PrometheusClient:
    global _prometheus
    if _prometheus is None:
        _prometheus = PrometheusClient()
    return _prometheus
