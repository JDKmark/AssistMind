"""Elasticsearch 客户端：_search 查询 + 失败降级。

对接 ES REST API（POST /{index}/_search），支持可选 basic auth。
失败降级：
- 未配置 ELASTICSEARCH_URL：is_connected=False，调用方（real 数据源）返回空数据
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


class ElasticsearchClient:
    """Elasticsearch REST 异步客户端。"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        if not settings.ELASTICSEARCH_URL:
            logger.warning("[ES] 未配置 ELASTICSEARCH_URL，数据源不可用")
            self._client = None
            return
        auth: tuple[str, str] | None = None
        if settings.ELASTICSEARCH_USERNAME:
            auth = (settings.ELASTICSEARCH_USERNAME, settings.ELASTICSEARCH_PASSWORD or "")
        try:
            self._client = httpx.AsyncClient(
                base_url=settings.ELASTICSEARCH_URL,
                timeout=settings.ELASTICSEARCH_TIMEOUT,
                auth=auth,
            )
            logger.info("[ES] 连接就绪，base_url=%s", settings.ELASTICSEARCH_URL)
        except Exception as e:
            self._client = None
            logger.warning("[ES] 客户端创建失败: %s", e)

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    async def search(
        self,
        index: str,
        body: dict[str, Any],
        size: int = 50,
    ) -> list[dict[str, Any]]:
        """执行 _search，返回 hits 的 _source 列表（原样，字段映射由调用方负责）。"""
        if not self._client:
            return []
        if is_open("elasticsearch"):
            logger.warning("[ES] 断路器 Open，跳过 search")
            return []
        try:
            async def _do_search() -> list[dict[str, Any]]:
                payload = dict(body)
                payload["size"] = size
                resp = await self._client.post(f"/{index}/_search", json=payload)
                resp.raise_for_status()
                data = resp.json()
                hits = data.get("hits", {}).get("hits", [])
                return [h.get("_source", {}) for h in hits]

            return await call_with_breaker("elasticsearch", _do_search)
        except CircuitBreakerOpenError:
            logger.warning("[ES] search 断路器 Open")
            return []
        except Exception as e:
            logger.warning("[ES] search 失败: %s", e)
            return []

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


_elasticsearch: ElasticsearchClient | None = None


def get_elasticsearch() -> ElasticsearchClient:
    global _elasticsearch
    if _elasticsearch is None:
        _elasticsearch = ElasticsearchClient()
    return _elasticsearch
