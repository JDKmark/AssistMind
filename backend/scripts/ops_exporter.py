"""演示业务指标 exporter：为运维诊断工作台提供真实 Prometheus 数据源。

纯标准库实现 Prometheus 文本格式 /metrics 端点（零第三方依赖），
导出 5 个业务指标 × 5 个服务。指标值随时间平滑波动（确定性 sin/cos），
接近真实业务负载形态，供诊断链路（Prometheus → 后端 → 前端）演示。

运行：python ops_exporter.py（默认 0.0.0.0:9100）
"""

from __future__ import annotations

import math
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = 9100

# 服务基线（与 backend/app/core/ops/scenarios.py 的 BASELINE 保持一致）
SERVICES: dict[str, dict[str, float]] = {
    "api-gateway": {"cpu_usage": 35, "memory_usage": 55, "error_rate": 0.1, "latency_p95_ms": 120, "qps": 1500},
    "order-service": {"cpu_usage": 40, "memory_usage": 60, "error_rate": 0.2, "latency_p95_ms": 200, "qps": 800},
    "inventory-service": {"cpu_usage": 30, "memory_usage": 50, "error_rate": 0.1, "latency_p95_ms": 80, "qps": 600},
    "payment-service": {"cpu_usage": 45, "memory_usage": 65, "error_rate": 0.1, "latency_p95_ms": 150, "qps": 500},
    "user-service": {"cpu_usage": 25, "memory_usage": 45, "error_rate": 0.05, "latency_p95_ms": 60, "qps": 900},
}

METRIC_HELP = {
    "cpu_usage": "CPU 使用率（%）",
    "memory_usage": "内存使用率（%）",
    "error_rate": "错误率（%）",
    "latency_p95_ms": "P95 延迟（毫秒）",
    "qps": "每秒请求数",
}


def _wave(ts: float, base: float, phase: int) -> float:
    """确定性平滑波动：慢波 + 快波叠加，模拟真实负载起伏。"""
    slow = math.sin((ts + phase * 997) / 900.0)
    fast = math.cos((ts + phase * 313) / 180.0)
    return base * (1 + 0.08 * (slow + 0.4 * fast) / 1.4)


def render_metrics() -> str:
    now = time.time()
    lines: list[str] = []
    for metric, help_text in METRIC_HELP.items():
        lines.append(f"# HELP ops_{metric} {help_text}")
        lines.append(f"# TYPE ops_{metric} gauge")
    for svc, baselines in SERVICES.items():
        phase = hash(svc) % 1000
        for metric, base in baselines.items():
            value = _wave(now, base, phase)
            if metric in ("cpu_usage", "memory_usage", "error_rate"):
                value = max(0.0, min(100.0, value))
            elif metric == "latency_p95_ms":
                value = max(1.0, value)
            else:  # qps
                value = max(0.0, value)
            lines.append(f'ops_{metric}{{service="{svc}"}} {value:.4f}')
    return "\n".join(lines) + "\n"


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = render_metrics().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        # 静默请求日志，避免容器日志刷屏
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), MetricsHandler)
    print(f"[ops-exporter] listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()
