"""运维数据源门面：统一查询入口，按配置选择实现。

消费方 import 路径不变（app.core.ops.data_source），调用改为 await。

选择逻辑（OPS_DATA_SOURCE）：
- mock: 恒用预置故障场景模拟数据（MockOpsDataSource）
- real: 恒用真实数据源（RealOpsDataSource，Prometheus/ELK），单源失败走各方法降级 + degraded
- auto: 配置了 PROMETHEUS_URL 且真实数据源健康探测通过 → real；否则 mock（logger.warning 降级）
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.core.ops.base import OpsDataSource
from app.core.ops.mock_source import MockOpsDataSource
from app.core.ops.real_source import RealOpsDataSource

logger = logging.getLogger(__name__)
settings = get_settings()

# 已解析的数据源实例与实际生效模式（进程内缓存；测试可调 reset_source() 重置）
_source: OpsDataSource | None = None
_source_mode: str | None = None


async def _resolve_source() -> OpsDataSource:
    """按配置解析并缓存数据源实现。"""
    global _source, _source_mode
    if _source is not None:
        return _source
    mode = (settings.OPS_DATA_SOURCE or "auto").lower()
    if mode == "mock":
        _source = MockOpsDataSource()
        _source_mode = "mock"
        logger.info("[Ops] 数据源=mock（OPS_DATA_SOURCE=mock）")
    elif mode == "real":
        _source = RealOpsDataSource()
        _source_mode = "real"
        logger.info("[Ops] 数据源=real（OPS_DATA_SOURCE=real）")
    else:  # auto
        if settings.PROMETHEUS_URL:
            real = RealOpsDataSource()
            await real.connect()
            if real.is_available:
                _source = real
                _source_mode = "real"
                logger.info("[Ops] 数据源=real（auto 模式，Prometheus 健康探测通过）")
            else:
                _source = MockOpsDataSource()
                _source_mode = "mock"
                logger.warning("[Ops] 真实数据源不可用，auto 模式降级为 mock")
        else:
            _source = MockOpsDataSource()
            _source_mode = "mock"
            logger.info("[Ops] 未配置 PROMETHEUS_URL，auto 模式使用 mock")
    return _source


def reset_source() -> None:
    """重置数据源缓存（测试隔离用）。"""
    global _source, _source_mode
    _source = None
    _source_mode = None


async def get_source_mode() -> str:
    """返回实际生效的数据源模式（mock/real），供 API 透传给前端展示。"""
    await _resolve_source()
    return _source_mode or "mock"


async def set_active_scenario(name: str | None) -> str | None:
    """设置活动故障场景。name=None 恢复无故障基线。返回生效场景名。"""
    return await (await _resolve_source()).set_active_scenario(name)


async def get_active_scenario() -> Any:
    """返回当前活动场景对象（mock 返回 OpsScenario；real 返回 None）。"""
    return await (await _resolve_source()).get_active_scenario()


async def list_scenarios() -> list[dict[str, str]]:
    """列出预置场景（供前端选择/演示）。"""
    return await (await _resolve_source()).list_scenarios()


async def list_services() -> list[str]:
    return await (await _resolve_source()).list_services()


async def list_metrics(service: str) -> list[str]:
    return await (await _resolve_source()).list_metrics(service)


async def list_hosts(service: str) -> list[str]:
    """列出服务运行所在的主机/实例（CMDB 部署拓扑）。"""
    return await (await _resolve_source()).list_hosts(service)


async def query_metric(
    service: str,
    metric: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[dict[str, Any]]:
    """查询时序指标 [{ts, value}]。默认最近 2 小时。"""
    return await (await _resolve_source()).query_metric(service, metric, start_ts, end_ts)


async def search_logs(
    service: str | None = None,
    keyword: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """搜索日志，按时间倒序。"""
    return await (await _resolve_source()).search_logs(service, keyword, start_ts, end_ts, limit)


async def query_changes(
    service: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """查询变更记录（部署/配置/扩容）。"""
    return await (await _resolve_source()).query_changes(service, start_ts, end_ts, limit)


async def get_alerts(
    service: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[dict[str, Any]]:
    """查询告警。"""
    return await (await _resolve_source()).get_alerts(service, start_ts, end_ts)
