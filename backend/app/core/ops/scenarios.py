"""预置电商微服务故障场景。

每个场景定义：涉及服务、指标异常形态、错误日志、变更记录、告警、根因。
data_source.py 据此生成"看起来真实"的时序数据。

场景列表：
- conn_pool_exhausted：inventory-service 连接池耗尽 → order-service 下单失败率上升
- slow_sql：payment-service 慢 SQL → api-gateway 延迟升高
- memory_leak：user-service 内存泄漏 → 登录超时、实例重启
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ===== 服务拓扑 =====
SERVICES: list[str] = [
    "api-gateway",
    "order-service",
    "inventory-service",
    "payment-service",
    "user-service",
]

# 各服务可用指标
METRICS: list[str] = [
    "cpu_usage",
    "memory_usage",
    "error_rate",
    "latency_p95",
    "qps",
]

# 正常基线（百分比 / 错误率% / 延迟ms / QPS）
BASELINE: dict[str, dict[str, float]] = {
    "api-gateway": {"cpu_usage": 35, "memory_usage": 55, "error_rate": 0.1, "latency_p95": 120, "qps": 1200},
    "order-service": {"cpu_usage": 40, "memory_usage": 60, "error_rate": 0.2, "latency_p95": 200, "qps": 300},
    "inventory-service": {"cpu_usage": 30, "memory_usage": 50, "error_rate": 0.1, "latency_p95": 80, "qps": 800},
    "payment-service": {"cpu_usage": 45, "memory_usage": 65, "error_rate": 0.1, "latency_p95": 150, "qps": 200},
    "user-service": {"cpu_usage": 25, "memory_usage": 45, "error_rate": 0.05, "latency_p95": 60, "qps": 500},
}


@dataclass
class MetricAnomaly:
    """单个指标的异常形态。peak 为故障峰值（与基线同单位）。"""

    service: str
    metric: str
    peak: float
    ramp_seconds: int = 300  # 爬坡时间（秒）


@dataclass
class LogEntry:
    """预置日志。offset_seconds 为相对 now 的负偏移（过去）。"""

    service: str
    offset_seconds: int
    level: str
    message: str
    trace_id: str = ""


@dataclass
class ChangeEntry:
    """预置变更记录。"""

    service: str
    offset_seconds: int
    type: str  # deploy | config | scale
    content: str


@dataclass
class AlertEntry:
    """预置告警。"""

    alert_id: str
    service: str
    metric: str
    severity: str  # critical | warning
    offset_seconds: int
    message: str


@dataclass
class OpsScenario:
    """完整故障场景定义。"""

    name: str
    title: str
    symptoms: list[str]
    anomalies: list[MetricAnomaly] = field(default_factory=list)
    logs: list[LogEntry] = field(default_factory=list)
    changes: list[ChangeEntry] = field(default_factory=list)
    alerts: list[AlertEntry] = field(default_factory=list)
    root_cause: str = ""
    evidence_keywords: list[str] = field(default_factory=list)


# =====================================================================
# 场景 1：inventory-service 连接池耗尽
# =====================================================================
SCENARIO_CONN_POOL = OpsScenario(
    name="conn_pool_exhausted",
    title="order-service 下单失败率上升（inventory-service 连接池耗尽）",
    symptoms=[
        "用户下单时提示「库存查询失败，请稍后重试」",
        "订单服务 error_rate 从 0.2% 飙升至 8%+",
        "库存接口延迟从 80ms 升至 900ms+",
    ],
    anomalies=[
        MetricAnomaly("order-service", "error_rate", 8.5),
        MetricAnomaly("order-service", "latency_p95", 1800),
        MetricAnomaly("inventory-service", "error_rate", 3.2),
        MetricAnomaly("inventory-service", "latency_p95", 900),
        MetricAnomaly("inventory-service", "qps", 150),  # 大量请求堆积，有效 QPS 下降
    ],
    logs=[
        LogEntry("inventory-service", -900, "ERROR", "HikariPool-1 - Connection is not available, request timed out after 30000ms", "tr-8f2a"),
        LogEntry("inventory-service", -840, "ERROR", "HikariPool-1 - Connection is not available, request timed out after 30000ms", "tr-8f2b"),
        LogEntry("inventory-service", -600, "WARN", "HikariPool-1 - Pool stats: total=10, active=10, idle=0, waiting=256", "tr-9c01"),
        LogEntry("inventory-service", -300, "WARN", "HikariPool-1 - Pool stats: total=10, active=10, idle=0, waiting=512", "tr-9c02"),
        LogEntry("order-service", -600, "ERROR", "Remote call failed: inventory-service /api/inventory/check timeout after 30000ms", "tr-8f2c"),
        LogEntry("order-service", -300, "ERROR", "Remote call failed: inventory-service /api/inventory/check timeout after 30000ms", "tr-9c03"),
        LogEntry("order-service", -120, "ERROR", "Order create failed: inventory check error, order_id=SO202608040001", "tr-9c04"),
    ],
    changes=[
        ChangeEntry("inventory-service", -3600, "config", "数据库连接池 max_pool_size 由 50 调整为 10（优化资源占用）"),
    ],
    alerts=[
        AlertEntry("alert-001", "order-service", "error_rate", "critical", -600, "order-service error_rate 超过阈值 5%（当前 8.5%）"),
        AlertEntry("alert-002", "inventory-service", "latency_p95", "warning", -900, "inventory-service latency_p95 超过阈值 500ms（当前 900ms）"),
    ],
    root_cause=(
        "inventory-service 在一小时前执行配置变更，将数据库连接池 max_pool_size 从 50 缩小到 10。"
        "连接池过小导致库存查询连接被快速耗尽，库存接口大面积超时；"
        "order-service 同步调用库存接口失败，下单失败率随之飙升。"
    ),
    evidence_keywords=["connection pool", "pool stats", "timed out", "inventory check", "max_pool_size"],
)


# =====================================================================
# 场景 2：payment-service 慢 SQL
# =====================================================================
SCENARIO_SLOW_SQL = OpsScenario(
    name="slow_sql",
    title="api-gateway 延迟升高（payment-service 慢 SQL）",
    symptoms=[
        "支付页面加载明显变慢，订单支付超时",
        "api-gateway latency_p95 从 120ms 升至 2500ms",
        "payment-service 数据库 CPU 占用持续高位",
    ],
    anomalies=[
        MetricAnomaly("api-gateway", "latency_p95", 2500),
        MetricAnomaly("payment-service", "cpu_usage", 88),
        MetricAnomaly("payment-service", "latency_p95", 2000),
        MetricAnomaly("payment-service", "error_rate", 2.5),
    ],
    logs=[
        LogEntry("payment-service", -1500, "WARN", "Slow query detected: SELECT * FROM payment_order WHERE status='PAID' ORDER BY created_at DESC LIMIT 20, took 8423ms", "tr-pay-01"),
        LogEntry("payment-service", -1200, "WARN", "Slow query detected: SELECT * FROM payment_order WHERE status='PAID' ORDER BY created_at DESC LIMIT 20, took 9130ms", "tr-pay-02"),
        LogEntry("payment-service", -600, "ERROR", "DB connection wait timeout: HikariPool-1 - Connection is not available, request timed out", "tr-pay-03"),
        LogEntry("payment-service", -300, "ERROR", "Payment query failed: java.sql.SQLException: Lock wait timeout exceeded", "tr-pay-04"),
        LogEntry("api-gateway", -300, "WARN", "Upstream timeout: payment-service /api/payment/query elapsed 30100ms", "tr-gw-01"),
    ],
    changes=[
        ChangeEntry("payment-service", -7200, "deploy", "发布 v2.3.1：新增「按状态查询支付记录」接口（含未优化查询）"),
    ],
    alerts=[
        AlertEntry("alert-101", "api-gateway", "latency_p95", "critical", -600, "api-gateway latency_p95 超过阈值 1000ms（当前 2500ms）"),
        AlertEntry("alert-102", "payment-service", "cpu_usage", "warning", -900, "payment-service cpu_usage 超过阈值 80%（当前 88%）"),
    ],
    root_cause=(
        "payment-service v2.3.1 新增的「按状态查询支付记录」接口未对 status 字段建索引，"
        "高并发下大量全表扫描产生慢 SQL，拖垮数据库连接与 CPU；"
        "支付接口延迟上升传导到 api-gateway，导致整体延迟升高。"
    ),
    evidence_keywords=["slow query", "payment_order", "lock wait", "upstream timeout"],
)


# =====================================================================
# 场景 3：user-service 内存泄漏
# =====================================================================
SCENARIO_MEMORY_LEAK = OpsScenario(
    name="memory_leak",
    title="user-service 登录超时与实例重启（内存泄漏）",
    symptoms=[
        "用户登录偶发超时，需要重试",
        "user-service 实例频繁 OOM 重启",
        "内存占用持续爬升至 95%",
    ],
    anomalies=[
        MetricAnomaly("user-service", "memory_usage", 96),
        MetricAnomaly("user-service", "cpu_usage", 72),
        MetricAnomaly("user-service", "error_rate", 4.0),
        MetricAnomaly("user-service", "latency_p95", 1500),
    ],
    logs=[
        LogEntry("user-service", -2400, "ERROR", "OutOfMemoryError: Java heap space", "tr-usr-01"),
        LogEntry("user-service", -1800, "ERROR", "GC overhead limit exceeded", "tr-usr-02"),
        LogEntry("user-service", -1200, "WARN", "Restarting instance user-service-7f9c due to OOM", "tr-usr-03"),
        LogEntry("user-service", -600, "ERROR", "Session store size=485000, growing unbounded", "tr-usr-04"),
        LogEntry("user-service", -300, "ERROR", "Login request timeout: session lookup blocked in GC", "tr-usr-05"),
    ],
    changes=[
        ChangeEntry("user-service", -10800, "deploy", "发布 v3.0.0：引入分布式会话缓存（无 TTL 清理）"),
    ],
    alerts=[
        AlertEntry("alert-201", "user-service", "memory_usage", "critical", -1200, "user-service memory_usage 超过阈值 90%（当前 96%）"),
        AlertEntry("alert-202", "user-service", "error_rate", "warning", -300, "user-service error_rate 超过阈值 3%（当前 4%）"),
    ],
    root_cause=(
        "user-service v3.0.0 引入的分布式会话缓存未设置 TTL 与容量上限，"
        "session 数据无限增长导致堆内存耗尽，频繁 OOM 与实例重启，"
        "登录请求在 GC 停顿中大量超时。"
    ),
    evidence_keywords=["OutOfMemoryError", "GC overhead", "session store", "restarting"],
)


SCENARIOS: dict[str, OpsScenario] = {
    s.name: s for s in (SCENARIO_CONN_POOL, SCENARIO_SLOW_SQL, SCENARIO_MEMORY_LEAK)
}

DEFAULT_SCENARIO = SCENARIO_CONN_POOL
