"""真实运维数据源：Prometheus（指标/服务）+ Alertmanager（告警）+ Elasticsearch（日志/变更）。

对接映射：
- query_metric → Prometheus /api/v1/query_range，PromQL 表达式从 app/data/ops_metric_exprs.json
  外置（支持 {service} 占位符，文件热加载，参照 intent_routes.json 外置约定）
- list_services → Prometheus label values（PROMETHEUS_SERVICE_LABEL）
- get_alerts → Alertmanager /api/v2/alerts（ALERTMANAGER_URL 未配置返回空）
- search_logs / query_changes → Elasticsearch _search（索引可配置，字段名容错映射）

失败降级：单源失败 → logger.warning + 返回空列表（supervisor 的 degraded 机制标记单源缺失；
auto 模式下整体不可用由门面降级 mock）。
场景语义：real 模式下场景仅 UI 展示（set 仅记录名字，不改变真实数据）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.core.infra.alertmanager import get_alertmanager
from app.core.infra.elasticsearch import get_elasticsearch
from app.core.infra.prometheus import get_prometheus
from app.core.ops.base import OpsDataSource

logger = logging.getLogger(__name__)
settings = get_settings()

# 指标表达式映射文件（相对本文件两层 ..，与 knowledge.py 的 scripts 路径约定一致）
_EXPR_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ops_metric_exprs.json")


class _ExprMap:
    """外置 PromQL 表达式映射：mtime 变化即热加载。"""

    def __init__(self, path: str) -> None:
        self._path = path
        self._mtime: float | None = None
        self._cache: dict[str, str] = {}

    def _reload_if_changed(self) -> None:
        try:
            mtime = os.path.getmtime(self._path)
        except OSError:
            logger.warning("[Ops] 指标表达式文件不可读: %s", self._path)
            return
        if self._cache and mtime == self._mtime:
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._cache = {str(k): str(v) for k, v in data.items()}
                self._mtime = mtime
        except Exception as e:
            logger.warning("[Ops] 指标表达式文件解析失败: %s", e)

    def get(self, metric: str) -> str | None:
        self._reload_if_changed()
        return self._cache.get(metric)

    def keys(self) -> list[str]:
        self._reload_if_changed()
        return list(self._cache.keys())


_expr_map = _ExprMap(_EXPR_FILE)


def _parse_ts(value: Any) -> int | None:
    """ISO8601 → epoch 秒（兼容 Z 后缀与带时区偏移）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _epoch_ms(ts: int) -> int:
    return ts * 1000


def _first(src: dict[str, Any], *keys: str) -> str:
    """按候选字段名容错取值（真实日志/变更索引字段名不统一）。"""
    for k in keys:
        v = src.get(k)
        if v is not None:
            return str(v)
    return ""


class RealOpsDataSource(OpsDataSource):
    """真实数据源：Prometheus / Alertmanager / Elasticsearch 组合。"""

    def __init__(self) -> None:
        self._active_name: str | None = None
        self._available: bool | None = None

    @property
    def source_mode(self) -> str:
        return "real"

    @property
    def is_available(self) -> bool:
        return bool(self._available)

    async def connect(self) -> None:
        """连接三个基础设施客户端并做一次 Prometheus 健康探测（auto 模式切换依据）。"""
        prom = get_prometheus()
        await prom.connect()
        await get_elasticsearch().connect()
        await get_alertmanager().connect()
        if not prom.is_connected:
            logger.warning("[Ops] Prometheus 不可用（未配置或连接失败），real 数据源不可用")
            self._available = False
            return
        self._available = await prom.ping()
        if not self._available:
            logger.warning("[Ops] Prometheus 健康探测失败，real 数据源不可用")

    async def set_active_scenario(self, name: str | None) -> str | None:
        """real 模式下场景仅 UI 展示语义：数据即真实状态，切换不改变数据。"""
        if name is None:
            self._active_name = None
            return None
        from app.core.ops.scenarios import SCENARIOS

        if name not in SCENARIOS:
            raise ValueError(f"未知场景: {name}")
        self._active_name = name
        return name

    async def get_active_scenario(self) -> None:
        return None

    async def list_scenarios(self) -> list[dict[str, str]]:
        """预置场景列表（仅 UI 展示用，与 mock 一致）。"""
        from app.core.ops.scenarios import SCENARIOS

        return [
            {"name": s.name, "title": s.title, "symptoms": s.symptoms}
            for s in SCENARIOS.values()
        ]

    async def list_services(self) -> list[str]:
        prom = get_prometheus()
        if not prom.is_connected:
            return []
        return await prom.label_values(settings.PROMETHEUS_SERVICE_LABEL)

    async def list_metrics(self, service: str) -> list[str]:
        return _expr_map.keys()

    async def list_hosts(self, service: str) -> list[str]:
        """Prometheus instance 标签全量清单。

        真实环境 service↔host 映射依赖 CMDB/抓取配置的数据形态（如
        instance 标签带服务前缀），此处返回全部已知实例，调用方按需过滤。
        """
        prom = get_prometheus()
        if not prom.is_connected:
            return []
        return await prom.label_values("instance")

    async def query_metric(
        self,
        service: str,
        metric: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """Prometheus Range Query。表达式按 {service} 占位符替换，未知指标返回空。"""
        expr = _expr_map.get(metric)
        if expr is None:
            return []
        prom = get_prometheus()
        if not prom.is_connected:
            return []
        now = int(time.time())
        end_ts = end_ts or now
        start_ts = start_ts or (now - 7200)
        return await prom.query_range(expr.replace("{service}", service), start_ts, end_ts, step=60)

    async def search_logs(
        self,
        service: str | None = None,
        keyword: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """ES 日志检索：query_string 关键词 + 时间 range，按 @timestamp 倒序。"""
        es = get_elasticsearch()
        if not es.is_connected:
            return []
        now = int(time.time())
        end_ts = end_ts or now
        start_ts = start_ts or (now - 7200)
        must: list[dict[str, Any]] = []
        if keyword:
            must.append({"query_string": {"query": f"*{keyword}*", "default_field": "message"}})
        filters: list[dict[str, Any]] = [
            {"range": {"@timestamp": {"gte": _epoch_ms(start_ts), "lte": _epoch_ms(end_ts)}}}
        ]
        if service:
            filters.append({"term": {"service.keyword": service}})
        body = {
            "query": {"bool": {"must": must, "filter": filters}},
            "sort": [{"@timestamp": "desc"}],
        }
        sources = await es.search(settings.ELASTICSEARCH_INDEX, body, size=limit)
        return [
            {
                "ts": _parse_ts(s.get("@timestamp")) or int(time.time()),
                "service": _first(s, "service", "service_name", "app"),
                "level": _first(s, "level", "loglevel", "log.level"),
                "message": _first(s, "message", "msg", "log"),
                "trace_id": _first(s, "trace_id", "traceId"),
            }
            for s in sources
        ]

    async def query_changes(
        self,
        service: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """ES 变更索引检索（部署/配置/扩容记录）。"""
        es = get_elasticsearch()
        if not es.is_connected:
            return []
        now = int(time.time())
        end_ts = end_ts or now
        start_ts = start_ts or (now - 86400)
        filters: list[dict[str, Any]] = [
            {"range": {"@timestamp": {"gte": _epoch_ms(start_ts), "lte": _epoch_ms(end_ts)}}}
        ]
        if service:
            filters.append({"term": {"service.keyword": service}})
        body = {
            "query": {"bool": {"filter": filters}},
            "sort": [{"@timestamp": "desc"}],
        }
        sources = await es.search(settings.ELASTICSEARCH_CHANGE_INDEX, body, size=limit)
        return [
            {
                "ts": _parse_ts(s.get("@timestamp")) or int(time.time()),
                "service": _first(s, "service", "service_name", "app"),
                "type": _first(s, "type", "change_type", "event_type") or "config",
                "content": _first(s, "content", "change", "message"),
            }
            for s in sources
        ]

    async def get_alerts(
        self,
        service: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """Alertmanager 活动告警（v2 API），字段映射为前后端契约形状。"""
        am = get_alertmanager()
        if not am.is_connected:
            return []
        raw = await am.alerts()
        now = int(time.time())
        end_ts = end_ts or now
        start_ts = start_ts or (now - 86400)
        results: list[dict[str, Any]] = []
        for al in raw:
            labels = al.get("labels") or {}
            annotations = al.get("annotations") or {}
            ts = _parse_ts(al.get("startsAt"))
            if ts is None or ts < start_ts or ts > end_ts:
                continue
            svc = labels.get("service") or labels.get("job") or "unknown"
            if service and svc != service:
                continue
            alert_name = labels.get("alertname", "alert")
            results.append(
                {
                    "alert_id": al.get("fingerprint") or alert_name,
                    "service": svc,
                    "metric": labels.get("metric") or alert_name,
                    "severity": labels.get("severity", "warning"),
                    "ts": ts,
                    "message": (
                        annotations.get("summary")
                        or annotations.get("description")
                        or alert_name
                    ),
                }
            )
        results.sort(key=lambda x: x["ts"], reverse=True)
        return results[:50]
