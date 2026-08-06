"""运维数据源抽象接口。

统一 mock（预置场景模拟）与 real（Prometheus/ELK 真实数据）两种实现的查询契约。
返回形状为前后端契约，两种实现必须保持一致：
- query_metric → [{ts, value}]
- search_logs → [{ts, service, level, message, trace_id}]
- query_changes → [{ts, service, type, content}]
- get_alerts → [{alert_id, service, metric, severity, ts, message}]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class OpsDataSource(ABC):
    """运维数据源接口：指标/日志/变更/告警统一查询。"""

    @property
    @abstractmethod
    def source_mode(self) -> str:
        """数据源模式标识：mock / real（供前端展示与门面切换）。"""

    @abstractmethod
    async def set_active_scenario(self, name: str | None) -> str | None:
        """设置活动故障场景。name=None 恢复无故障基线。返回生效场景名。

        real 模式下仅记录展示语义，不改变真实数据。
        """

    @abstractmethod
    async def get_active_scenario(self) -> Any:
        """返回当前活动场景对象（mock 返回 OpsScenario；real 无场景对象，返回 None）。"""

    @abstractmethod
    async def list_scenarios(self) -> list[dict[str, str]]:
        """列出预置场景（供前端选择/演示）。"""

    @abstractmethod
    async def list_services(self) -> list[str]:
        """列出服务列表。"""

    @abstractmethod
    async def list_metrics(self, service: str) -> list[str]:
        """列出某服务的可用指标。"""

    @abstractmethod
    async def list_hosts(self, service: str) -> list[str]:
        """列出服务运行所在的主机/实例（CMDB 部署拓扑）。

        mock 按服务预置映射；real 从 Prometheus instance 标签取全量清单
        （真实 service↔host 映射依赖 CMDB 数据形态，见实现注释）。
        """

    @abstractmethod
    async def query_metric(
        self,
        service: str,
        metric: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """查询时序指标 [{ts, value}]。默认最近 2 小时。"""

    @abstractmethod
    async def search_logs(
        self,
        service: str | None = None,
        keyword: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """搜索日志，按时间倒序。"""

    @abstractmethod
    async def query_changes(
        self,
        service: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """查询变更记录（部署/配置/扩容）。"""

    @abstractmethod
    async def get_alerts(
        self,
        service: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """查询告警。"""
