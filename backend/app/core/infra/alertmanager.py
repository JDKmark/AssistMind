"""Alertmanager 客户端：活动告警查询 + 失败降级。

对接 Alertmanager HTTP API v2（GET /api/v2/alerts），返回原始告警结构，
字段映射（alert_id/service/metric/severity/ts/message）由 real 数据源负责。
失败降级：
- 未配置 ALERTMANAGER_URL：is_connected=False，调用方（real 数据源）返回空列表
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


class AlertmanagerClient:
    """Alertmanager HTTP API v2 异步客户端。"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        if not settings.ALERTMANAGER_URL:
            logger.warning("[Alertmanager] 未配置 ALERTMANAGER_URL，数据源不可用")
            self._client = None
            return
        try:
            self._client = httpx.AsyncClient(
                base_url=settings.ALERTMANAGER_URL,
                timeout=settings.PROMETHEUS_TIMEOUT,
            )
            logger.info("[Alertmanager] 连接就绪，base_url=%s", settings.ALERTMANAGER_URL)
        except Exception as e:
            self._client = None
            logger.warning("[Alertmanager] 客户端创建失败: %s", e)

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    async def alerts(self) -> list[dict[str, Any]]:
        """查询当前活动告警（GET /api/v2/alerts），返回原始 Alertmanager 结构。"""
        if not self._client:
            return []
        if is_open("alertmanager"):
            logger.warning("[Alertmanager] 断路器 Open，跳过 alerts")
            return []
        try:
            async def _do_alerts() -> list[dict[str, Any]]:
                resp = await self._client.get("/api/v2/alerts")
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list):
                    logger.warning("[Alertmanager] alerts 返回异常结构: %s", type(data).__name__)
                    return []
                return data

            return await call_with_breaker("alertmanager", _do_alerts)
        except CircuitBreakerOpenError:
            logger.warning("[Alertmanager] alerts 断路器 Open")
            return []
        except Exception as e:
            logger.warning("[Alertmanager] alerts 失败: %s", e)
            return []

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


_alertmanager: AlertmanagerClient | None = None


def get_alertmanager() -> AlertmanagerClient:
    global _alertmanager
    if _alertmanager is None:
        _alertmanager = AlertmanagerClient()
    return _alertmanager
