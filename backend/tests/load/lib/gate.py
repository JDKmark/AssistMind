"""CI 性能门禁：读取 Locust CSV 统计，对比阈值，劣化即失败（spec 3.10）。

用法：
    # 跑完 smoke 后
    python -m lib.gate --prefix smoke --p95-ms 8000 --ttft-p95-ms 3000
    # 或显式指定阈值文件
    python -m lib.gate --prefix smoke --thresholds thresholds.json

判定：
- p95（端到端，排除 ``:ttft`` 附加行）> --p95-ms → 失败
- TTFT p95（``*:ttft`` 行）> --ttft-p95-ms → 失败
- 失败率 > --max-fail-ratio → 失败

与 RAGAS 的关系：RAGAS 守「答得对不对」，本门禁守「快不快、稳不稳」，两者并列。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Locust CSV 各版本百分位列名兼容
_P95_KEYS = ("95%", "p95", "95")
_FAIL_KEYS = ("Failure Count", "failure_count")
_COUNT_KEYS = ("Request Count", "request_count")


def _read_stats(prefix: str) -> list[dict[str, str]]:
    path = Path(f"{prefix}_stats.csv")
    if not path.exists():
        raise FileNotFoundError(f"未找到 Locust 统计文件：{path}（确认 --csv {prefix} 已生效）")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _pick(row: dict[str, str], keys: tuple[str, ...], default: str = "0") -> str:
    for key in keys:
        if key in row and row[key] not in ("", None):
            return str(row[key]).strip()
    return default


def _p95(row: dict[str, str]) -> float:
    raw = _pick(row, _P95_KEYS)
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _f(row: dict[str, str], keys: tuple[str, ...]) -> float:
    try:
        return float(_pick(row, keys))
    except ValueError:
        return 0.0


def evaluate(prefix: str, *, p95_ms: float, ttft_p95_ms: float, max_fail_ratio: float) -> tuple[bool, list[str]]:
    rows = _read_stats(prefix)
    lines: list[str] = []
    ok = True
    total_req = total_fail = 0.0

    agg_p95 = 0.0
    agg_ttft_p95 = 0.0

    for row in rows:
        name = (row.get("Name") or row.get("name") or "").strip()
        if not name or name.lower() in ("aggregated", "total"):
            continue
        req = _f(row, _COUNT_KEYS)
        fail = _f(row, _FAIL_KEYS)
        if not name.endswith(":ttft"):
            total_req += req
            total_fail += fail
        p95 = _p95(row)
        if name.endswith(":ttft"):
            agg_ttft_p95 = max(agg_ttft_p95, p95)
        else:
            agg_p95 = max(agg_p95, p95)
        lines.append(f"  {name:<32} req={req:>8.0f} fail={fail:>6.0f} p95={p95:>9.1f}ms")

    fail_ratio = (total_fail / total_req) if total_req else 0.0

    if agg_p95 > p95_ms:
        ok = False
        lines.append(f"  ❌ p95 {agg_p95:.1f}ms > 阈值 {p95_ms:.1f}ms")
    else:
        lines.append(f"  ✅ p95 {agg_p95:.1f}ms ≤ 阈值 {p95_ms:.1f}ms")

    if agg_ttft_p95 > ttft_p95_ms:
        ok = False
        lines.append(f"  ❌ TTFT p95 {agg_ttft_p95:.1f}ms > 阈值 {ttft_p95_ms:.1f}ms")
    else:
        lines.append(f"  ✅ TTFT p95 {agg_ttft_p95:.1f}ms ≤ 阈值 {ttft_p95_ms:.1f}ms")

    if fail_ratio > max_fail_ratio:
        ok = False
        lines.append(f"  ❌ 失败率 {fail_ratio:.2%} > 阈值 {max_fail_ratio:.2%}")
    else:
        lines.append(f"  ✅ 失败率 {fail_ratio:.2%} ≤ 阈值 {max_fail_ratio:.2%}")

    return ok, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Locust CSV 性能门禁")
    parser.add_argument("--prefix", default="smoke", help="Locust --csv 前缀（默认 smoke）")
    parser.add_argument("--p95-ms", type=float, default=8000.0)
    parser.add_argument("--ttft-p95-ms", type=float, default=3000.0)
    parser.add_argument("--max-fail-ratio", type=float, default=0.02)
    parser.add_argument("--thresholds", help="JSON 阈值文件（覆盖上面三项默认值）")
    args = parser.parse_args(argv)

    p95, ttft, ratio = args.p95_ms, args.ttft_p95_ms, args.max_fail_ratio
    if args.thresholds:
        data = json.loads(Path(args.thresholds).read_text(encoding="utf-8"))
        p95 = float(data.get("p95_ms", p95))
        ttft = float(data.get("ttft_p95_ms", ttft))
        ratio = float(data.get("max_fail_ratio", ratio))

    try:
        ok, lines = evaluate(args.prefix, p95_ms=p95, ttft_p95_ms=ttft, max_fail_ratio=ratio)
    except FileNotFoundError as e:
        print(f"[Gate] {e}", file=sys.stderr)
        return 2

    print(f"[Gate] 性能门禁对比（prefix={args.prefix}）")
    for line in lines:
        print(line)
    print("[Gate] 结果：", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
