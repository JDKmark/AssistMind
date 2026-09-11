"""压测结果落盘（CSV + 汇总），供基线报告与拐点分析使用。

落盘口径（spec 3.9，与 T5 指标一致，避免两套口径）：
- 吞吐：每请求一行，按时间戳可聚合 QPS
- 延迟：ttft_ms / total_ms（p50/p95/p99 由 CSV 或 Locust 报告计算）
- 稳定性：status_code / error / SSE 事件序列 / 中断（无 done）
- 降级：degraded 列表 + n_degraded

写盘是**旁路**：任何 IO 失败都只记 warning，绝不打断压测。
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FIELDS = [
    "ts",
    "scenario",
    "query_len",
    "status_code",
    "ttft_ms",
    "total_ms",
    "intent",
    "from_cache",
    "degraded",
    "n_degraded",
    "events",
    "answer_len",
    "tool_calls",
    "ok",
    "error",
]

_lock = threading.Lock()
_writer: Any = None
_fh: Any = None
_path: Path | None = None
_counts: dict[str, int] = {}


def init(output_dir: str | None = None, *, tag: str = "load") -> None:
    """初始化 CSV 落盘（幂等；重复调用沿用同一文件）。"""
    global _writer, _fh, _path
    if _writer is not None:
        return
    out_dir = Path(output_dir or os.getenv("LOAD_CSV_DIR") or ".").resolve()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        _path = out_dir / f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        _fh = _path.open("w", newline="", encoding="utf-8")
        _writer = csv.DictWriter(_fh, fieldnames=_FIELDS)
        _writer.writeheader()
        logger.info("[LoadIngest] 明细 CSV: %s", _path)
    except Exception as e:  # noqa: BLE001 - 旁路
        logger.warning("[LoadIngest] CSV 初始化失败（忽略，不影响压测）: %s", e)
        _writer = None


def record(row: dict[str, Any]) -> None:
    """追加一行明细。"""
    if _writer is None:
        return
    try:
        payload = {k: row.get(k, "") for k in _FIELDS}
        payload["ts"] = row.get("ts") or f"{time.time():.3f}"
        with _lock:
            _writer.writerow(payload)
            _fh.flush()
        scenario = str(payload.get("scenario", "unknown"))
        _counts[scenario] = _counts.get(scenario, 0) + 1
    except Exception as e:  # noqa: BLE001 - 旁路
        logger.warning("[LoadIngest] CSV 写入失败（忽略）: %s", e)


def summary() -> dict[str, int]:
    """各场景已采集行数（Locust 停止事件里打印，便于判断是否有数据）。"""
    return dict(_counts)


def close() -> None:
    global _fh, _writer
    try:
        if _fh is not None:
            _fh.flush()
            _fh.close()
            logger.info("[LoadIngest] 明细已落盘: %s（%s）", _path, _counts)
    except Exception as e:  # noqa: BLE001
        logger.warning("[LoadIngest] 关闭失败: %s", e)
    finally:
        _fh = None
        _writer = None
