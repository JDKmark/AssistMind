"""模拟运维数据源：基于预置故障场景的确定性数据。

模拟 Prometheus / ELK / 变更平台 / 告警中心的查询入口。
数据形态由活动场景决定：
- 无活动场景：返回正常基线数据（小幅噪声）
- 有活动场景：故障时间窗内返回异常数据

时间模型：
- 故障时间窗 = [now - WINDOW_BEFORE_SEC, now - WINDOW_AFTER_SEC]（秒，负偏移表示过去）
- 指标步长 STEP_SEC = 60
- 数据确定性：基于时间戳的伪随机（sin/hash），测试可重复
"""

from __future__ import annotations

import math
import time
from typing import Any

from app.core.ops.base import OpsDataSource
from app.core.ops.scenarios import (
    BASELINE,
    METRICS,
    SCENARIOS,
    SERVICES,
    OpsScenario,
)

# 故障时间窗（相对 now）
WINDOW_BEFORE_SEC = 3600  # 故障开始于 1 小时前
WINDOW_AFTER_SEC = 120    # 故障持续到 2 分钟前
STEP_SEC = 60

# 服务 → 主机/实例映射（CMDB 部署拓扑，模拟）
HOSTS: dict[str, list[str]] = {
    "api-gateway": ["gw-prod-01", "gw-prod-02"],
    "order-service": ["order-prod-01", "order-prod-02", "order-prod-03"],
    "inventory-service": ["inv-prod-01", "inv-prod-02"],
    "payment-service": ["pay-prod-01", "pay-prod-02"],
    "user-service": ["user-prod-01", "user-prod-02"],
}


def _noise(ts: int, seed: int, amp: float = 0.06) -> float:
    """确定性噪声：基于时间戳的伪随机。"""
    return amp * math.sin((ts + seed) * 0.13) * math.cos(ts * 0.037 + seed)


def _in_window(ts: int, now: int) -> bool:
    """判断时间点是否落在活动故障窗内。"""
    return now - WINDOW_BEFORE_SEC <= ts <= now - WINDOW_AFTER_SEC


class MockOpsDataSource(OpsDataSource):
    """模拟数据源：场景切换驱动数据形态。"""

    def __init__(self) -> None:
        # 活动场景（进程内；多 worker 可用 Redis 扩展，此处简化）
        self._active: OpsScenario | None = None

    @property
    def source_mode(self) -> str:
        return "mock"

    async def set_active_scenario(self, name: str | None) -> str | None:
        """设置活动故障场景。name=None 恢复无故障基线。返回生效场景名。"""
        if name is None:
            self._active = None
            return None
        scenario = SCENARIOS.get(name)
        if scenario is None:
            raise ValueError(f"未知场景: {name}")
        self._active = scenario
        return scenario.name

    async def get_active_scenario(self) -> OpsScenario | None:
        return self._active

    async def list_scenarios(self) -> list[dict[str, str]]:
        """列出全部预置场景（供前端选择/演示）。"""
        return [
            {"name": s.name, "title": s.title, "symptoms": s.symptoms}
            for s in SCENARIOS.values()
        ]

    async def list_services(self) -> list[str]:
        return list(SERVICES)

    async def list_metrics(self, service: str) -> list[str]:
        if service not in SERVICES:
            return []
        return list(METRICS)

    async def list_hosts(self, service: str) -> list[str]:
        """返回服务运行所在的主机列表（预置拓扑）。"""
        return list(HOSTS.get(service, []))

    def _value_at(self, scenario: OpsScenario | None, service: str, metric: str, ts: int, now: int) -> float:
        """计算某时刻指标值。"""
        base = BASELINE[service][metric]
        if scenario is None or not _in_window(ts, now):
            return round(base * (1 + _noise(ts, hash(service + metric) % 1000)), 2)

        # 故障窗内：基线 + 异常形态（线性爬坡到峰值，峰值处维持）
        anomaly = None
        for a in scenario.anomalies:
            if a.service == service and a.metric == metric:
                anomaly = a
                break
        if anomaly is None:
            # 无明确异常定义的服务/指标：小幅扰动
            return round(base * (1 + _noise(ts, hash(service + metric) % 1000, 0.15)), 2)

        elapsed = now - WINDOW_BEFORE_SEC
        progress = min(1.0, max(0.0, (ts - elapsed) / max(anomaly.ramp_seconds, 1)))
        # 峰值持续到 WINDOW_AFTER_SEC
        value = base + (anomaly.peak - base) * progress
        jitter = 1 + _noise(ts, hash(service + metric) % 1000, 0.05)
        return round(max(0.0, value * jitter), 2)

    async def query_metric(
        self,
        service: str,
        metric: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """查询时序指标。返回 [{ts, value}]，步长 60s。

        默认最近 2 小时。service/metric 非法返回空列表。
        """
        if service not in SERVICES or metric not in METRICS:
            return []
        now = int(time.time())
        end_ts = end_ts or now
        start_ts = start_ts or (now - 7200)
        scenario = self._active
        points: list[dict[str, Any]] = []
        ts = start_ts - (start_ts % STEP_SEC) + STEP_SEC
        while ts <= end_ts:
            points.append({"ts": ts, "value": self._value_at(scenario, service, metric, ts, now)})
            ts += STEP_SEC
        return points

    async def search_logs(
        self,
        service: str | None = None,
        keyword: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """搜索日志。service/keyword 过滤，时间范围过滤，按时间倒序。

        无活动场景时返回空（无故障即无异常日志）。
        有活动场景时返回预置日志（匹配过滤条件）。
        """
        scenario = self._active
        if scenario is None:
            return []
        now = int(time.time())
        end_ts = end_ts or now
        start_ts = start_ts or (now - 7200)

        results: list[dict[str, Any]] = []
        for log in scenario.logs:
            ts = now + log.offset_seconds
            if ts < start_ts or ts > end_ts:
                continue
            if service and log.service != service:
                continue
            if keyword and keyword.lower() not in log.message.lower():
                continue
            results.append(
                {
                    "ts": ts,
                    "service": log.service,
                    "level": log.level,
                    "message": log.message,
                    "trace_id": log.trace_id,
                }
            )
        results.sort(key=lambda x: x["ts"], reverse=True)
        return results[:limit]

    async def query_changes(
        self,
        service: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """查询变更记录（部署/配置/扩容）。"""
        scenario = self._active
        if scenario is None:
            return []
        now = int(time.time())
        end_ts = end_ts or now
        start_ts = start_ts or (now - 86400)

        results: list[dict[str, Any]] = []
        for ch in scenario.changes:
            ts = now + ch.offset_seconds
            if ts < start_ts or ts > end_ts:
                continue
            if service and ch.service != service:
                continue
            results.append(
                {"ts": ts, "service": ch.service, "type": ch.type, "content": ch.content}
            )
        results.sort(key=lambda x: x["ts"], reverse=True)
        return results[:limit]

    async def get_alerts(
        self,
        service: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """查询告警。无活动场景返回空。"""
        scenario = self._active
        if scenario is None:
            return []
        now = int(time.time())
        end_ts = end_ts or now
        start_ts = start_ts or (now - 86400)

        results: list[dict[str, Any]] = []
        for al in scenario.alerts:
            ts = now + al.offset_seconds
            if ts < start_ts or ts > end_ts:
                continue
            if service and al.service != service:
                continue
            results.append(
                {
                    "alert_id": al.alert_id,
                    "service": al.service,
                    "metric": al.metric,
                    "severity": al.severity,
                    "ts": ts,
                    "message": al.message,
                }
            )
        results.sort(key=lambda x: x["ts"], reverse=True)
        return results[:50]
