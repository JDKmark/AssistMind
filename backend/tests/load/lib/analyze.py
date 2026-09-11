"""压测结果分析：从明细 CSV 计算分阶指标、拐点候选与降级统计。

为什么不用 Locust 的 CSV 做全部统计：
- Locust 只落聚合值，拿不到「按阶段 × 按场景」的交叉口径，也拿不到 TTFT / 降级 / 缓存命中
- 明细 CSV 每请求一行（`lib/ingest.py`），一次聚合即可得到报告需要的全部四类指标

用法：
    python -m lib.analyze --csv results/load_20260911_001951.csv \
        --stages 10:180,25:180,50:180,100:180,200:180,400:180 \
        --json results/baseline_analysis.json --markdown results/baseline_analysis.md
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _pct(values: list[float], p: float) -> float:
    """线性插值分位数（p 为 0–100）。空列表返回 0。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (p / 100.0) * (len(ordered) - 1)
    low, high = math.floor(rank), math.ceil(rank)
    if low == high:
        return float(ordered[int(rank)])
    frac = rank - low
    return float(ordered[low]) * (1 - frac) + float(ordered[high]) * frac


@dataclass
class StageStat:
    users: int
    duration_s: int
    n: int = 0
    qps: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    ttft_p50_ms: float = 0.0
    ttft_p95_ms: float = 0.0
    ttft_n: int = 0
    err_rate: float = 0.0
    http_429: int = 0
    http_5xx: int = 0
    cache_hit_rate: float = 0.0
    degraded_total: int = 0
    sse_broken: int = 0
    by_scenario: dict[str, dict[str, float]] = field(default_factory=dict)


def _load_rows(csv_path: str) -> list[dict[str, str]]:
    with Path(csv_path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _fnum(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_stages(raw: str) -> list[tuple[int, int]]:
    stages: list[tuple[int, int]] = []
    for part in raw.split(","):
        part = part.strip()
        if part and ":" in part:
            users, dur = part.split(":", 1)
            stages.append((int(users), int(dur)))
    return stages


def analyze(rows: list[dict[str, str]], stages: list[tuple[int, int]]) -> dict:
    """按阶梯时间窗切分并计算指标。"""
    t0 = min(_fnum(r["ts"]) for r in rows) if rows else 0.0
    result_stages: list[StageStat] = []
    cursor = 0.0

    for users, duration in stages:
        lo, hi = t0 + cursor, t0 + cursor + duration
        window = [r for r in rows if lo <= _fnum(r["ts"]) < hi]
        cursor += duration

        stat = StageStat(users=users, duration_s=duration, n=len(window))
        if window:
            totals = [_fnum(r["total_ms"]) for r in window]
            ttft = [_fnum(r["ttft_ms"]) for r in window if _fnum(r["ttft_ms"]) >= 0]
            stat.qps = len(window) / duration
            stat.p50_ms = _pct(totals, 50)
            stat.p95_ms = _pct(totals, 95)
            stat.p99_ms = _pct(totals, 99)
            stat.ttft_n = len(ttft)
            stat.ttft_p50_ms = _pct(ttft, 50)
            stat.ttft_p95_ms = _pct(ttft, 95)

            ok = sum(1 for r in window if r.get("ok") == "1")
            stat.err_rate = 1 - ok / len(window)
            stat.http_429 = sum(
                1 for r in window if str(r.get("status_code")) == "429" or "http_429" in r.get("error", "")
            )
            stat.http_5xx = sum(
                1 for r in window if str(r.get("status_code", "")).startswith("5") or "http_5" in r.get("error", "")
            )
            stat.cache_hit_rate = sum(1 for r in window if r.get("from_cache")) / len(window)
            stat.degraded_total = sum(int(_fnum(r.get("n_degraded"))) for r in window)
            stat.sse_broken = sum(1 for r in window if "done" not in (r.get("events") or ""))

            for scenario in sorted({r["scenario"] for r in window}):
                sub = [r for r in window if r["scenario"] == scenario]
                sub_totals = [_fnum(r["total_ms"]) for r in sub]
                sub_ttft = [_fnum(r["ttft_ms"]) for r in sub if _fnum(r["ttft_ms"]) >= 0]
                stat.by_scenario[scenario] = {
                    "n": len(sub),
                    "qps": round(len(sub) / duration, 3),
                    "p50_ms": round(_pct(sub_totals, 50), 1),
                    "p95_ms": round(_pct(sub_totals, 95), 1),
                    "ttft_p50_ms": round(_pct(sub_ttft, 50), 1),
                    "ttft_p95_ms": round(_pct(sub_ttft, 95), 1),
                }
        result_stages.append(stat)

    # 拐点判定：QPS 相对上一阶增幅 < 20% 且 p95 增幅 > 50% 的最高阶（首个满足的位置）
    knee: dict | None = None
    for prev, cur in zip(result_stages, result_stages[1:], strict=False):
        if prev.qps <= 0:
            continue
        qps_gain = (cur.qps - prev.qps) / prev.qps
        p95_gain = (cur.p95_ms - prev.p95_ms) / prev.p95_ms if prev.p95_ms else 0.0
        if qps_gain < 0.20 and p95_gain > 0.50:
            knee = {
                "users": cur.users,
                "prev_users": prev.users,
                "qps": round(prev.qps, 3),
                "qps_gain": round(qps_gain, 3),
                "p95_ms": round(cur.p95_ms, 1),
                "p95_gain": round(p95_gain, 3),
            }
            break

    overall_total = [_fnum(r["total_ms"]) for r in rows]
    overall_ttft = [_fnum(r["ttft_ms"]) for r in rows if _fnum(r["ttft_ms"]) >= 0]
    degraded_counter: dict[str, int] = {}
    for r in rows:
        for item in (r.get("degraded") or "").split("|"):
            if item:
                degraded_counter[item] = degraded_counter.get(item, 0) + 1

    return {
        "total_requests": len(rows),
        "duration_s": round(cursor, 1),
        "overall": {
            "p50_ms": round(_pct(overall_total, 50), 1),
            "p95_ms": round(_pct(overall_total, 95), 1),
            "p99_ms": round(_pct(overall_total, 99), 1),
            "ttft_p50_ms": round(_pct(overall_ttft, 50), 1),
            "ttft_p95_ms": round(_pct(overall_ttft, 95), 1),
            "err_rate": round(1 - sum(1 for r in rows if r.get("ok") == "1") / len(rows), 4) if rows else 0,
            "cache_hit_rate": round(sum(1 for r in rows if r.get("from_cache")) / len(rows), 4) if rows else 0,
            "p95_stdev_ms": round(statistics.pstdev(overall_total), 1) if len(overall_total) > 1 else 0.0,
        },
        "stages": [asdict(s) for s in result_stages],
        "knee": knee,
        "degraded_counter": degraded_counter,
    }


def to_markdown(result: dict, title: str) -> str:
    lines = [f"# {title}", "", f"- 总请求数：{result['total_requests']}", ""]
    lines += [
        "| 并发 | 请求数 | QPS | p50(ms) | p95(ms) | p99(ms) | TTFT p50(ms) | TTFT p95(ms) | 错误率 | 429 | 5xx | 缓存命中 | 降级次数 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in result["stages"]:
        lines.append(
            f"| {s['users']} | {s['n']} | {s['qps']:.2f} | {s['p50_ms']:.0f} | {s['p95_ms']:.0f} | "
            f"{s['p99_ms']:.0f} | {s['ttft_p50_ms']:.0f} | {s['ttft_p95_ms']:.0f} | "
            f"{s['err_rate']:.2%} | {s['http_429']} | {s['http_5xx']} | {s['cache_hit_rate']:.1%} | {s['degraded_total']} |"
        )
    lines.append("")
    knee = result.get("knee")
    if knee:
        lines.append(
            f"**拐点判定**：{knee['prev_users']} → {knee['users']} 并发时，QPS 增幅仅 "
            f"{knee['qps_gain']:.1%}（上一阶 {knee['qps']:.2f} QPS），而 p95 上升 {knee['p95_gain']:.1%}"
            f"（{knee['p95_ms']:.0f}ms）。"
        )
    else:
        lines.append("**拐点判定**：未在测试区间内检出（吞吐仍随并发上升）。")
    if result.get("degraded_counter"):
        lines.append("")
        lines.append("降级触发分布：" + ", ".join(f"{k}={v}" for k, v in sorted(result["degraded_counter"].items())))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="压测明细 CSV 分析")
    parser.add_argument("--csv", help="明细 CSV；省略则取 results/load_*.csv 中最新的一个")
    parser.add_argument("--stages", required=True, help="阶梯定义 users:seconds,...")
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--markdown", dest="md_out")
    parser.add_argument("--title", default="阶梯加压结果")
    args = parser.parse_args(argv)

    csv_path = args.csv
    if not csv_path:
        candidates = sorted(glob.glob("results/load_*.csv"), key=lambda p: Path(p).stat().st_mtime)
        if not candidates:
            print("[Analyze] 未找到明细 CSV")
            return 2
        csv_path = candidates[-1]

    rows = _load_rows(csv_path)
    result = analyze(rows, _parse_stages(args.stages))
    result["source_csv"] = csv_path
    result["stages_spec"] = args.stages

    md = to_markdown(result, args.title)
    print(md)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[Analyze] JSON 已写入 {args.json_out}")
    if args.md_out:
        Path(args.md_out).write_text(md, encoding="utf-8")
        print(f"[Analyze] Markdown 已写入 {args.md_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
