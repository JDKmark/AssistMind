"""资源采样（压测期间旁路采集被测进程 CPU / 内存 / 线程 + 应用 inflight）。

用途：基线报告里的「资源与成本」一类指标（spec 3.9）。浸泡测试同样用它观察泄漏。

用法：
    # 按端口自动定位被测进程
    python -m lib.resource_sample --port 8002 --duration 120 --out results/res_sample.csv
    # 或显式指定 PID
    python -m lib.resource_sample --pid 12345 --duration 120
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
import urllib.request
from pathlib import Path

import psutil

_METRIC = "assistmind_inflight_requests"


def find_pid_by_port(port: int) -> int | None:
    """按监听端口定位进程 PID（找不到返回 None）。"""
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                return conn.pid
    except Exception as e:  # noqa: BLE001 - 权限受限时降级
        print(f"[ResSample] net_connections 失败: {e}", file=sys.stderr)
    return None


def read_inflight(base_url: str) -> float | None:
    """从 /metrics 读取 inflight 值（失败返回 None，不影响采样）。"""
    try:
        with urllib.request.urlopen(f"{base_url}/metrics", timeout=2) as resp:
            for line in resp.read().decode("utf-8", "ignore").splitlines():
                if line.startswith(_METRIC + " "):
                    return float(line.split(" ")[1])
    except Exception:  # noqa: BLE001
        return None
    return None


def sample(pid: int, duration: float, interval: float, out: str, base_url: str) -> None:
    proc = psutil.Process(pid)
    proc.cpu_percent(interval=None)  # 初始化基线
    rows: list[dict[str, object]] = []
    t_end = time.time() + duration
    print(f"[ResSample] 采样 PID={pid} name={proc.name()} {duration}s @ {interval}s -> {out}", flush=True)

    while time.time() < t_end:
        cpu = proc.cpu_percent(interval=None)
        try:
            rss = proc.memory_info().rss / 1024 / 1024
            threads = proc.num_threads()
        except psutil.Error:
            rss, threads = 0.0, 0
        rows.append(
            {
                "ts": f"{time.time():.3f}",
                "cpu_percent": round(cpu, 1),
                "rss_mb": round(rss, 1),
                "threads": threads,
                "inflight": read_inflight(base_url) if base_url else None,
            }
        )
        time.sleep(interval)

    path = Path(out)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ts", "cpu_percent", "rss_mb", "threads", "inflight"])
        writer.writeheader()
        writer.writerows(rows)

    if rows:
        cpu_vals = [float(r["cpu_percent"]) for r in rows]
        rss_vals = [float(r["rss_mb"]) for r in rows]
        inflights = [r["inflight"] for r in rows if r["inflight"] is not None]
        print(
            f"[ResSample] cpu avg={sum(cpu_vals) / len(cpu_vals):.1f}% peak={max(cpu_vals):.1f}% | "
            f"rss avg={sum(rss_vals) / len(rss_vals):.0f}MB peak={max(rss_vals):.0f}MB | "
            f"threads peak={max(int(r['threads']) for r in rows)} | "
            f"inflight peak={max(inflights) if inflights else 'n/a'}",
            flush=True,
        )
    print(f"[ResSample] 已写入 {out}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="压测资源采样")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--base-url", default="", help="用于读取 inflight 的应用地址，如 http://localhost:8002")
    parser.add_argument("--out", default="resource_sample.csv")
    args = parser.parse_args(argv)

    pid = args.pid or find_pid_by_port(args.port)
    if not pid:
        print(f"[ResSample] 未找到监听 {args.port} 的进程", file=sys.stderr)
        return 2
    sample(pid, args.duration, args.interval, args.out, args.base_url)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
