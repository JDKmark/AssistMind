"""Locust 入口：按 ``LOAD_SCENARIO`` 装载 S1–S7 之一，并按需启用阶梯加压 Shape。

用法（在 backend/tests/load 目录下）：
    # 冒烟（CI 门禁：1–2 分钟小负载）
    LOAD_SCENARIO=s4 locust -f locustfile.py --headless --config profiles/smoke.conf

    # 阶梯加压找拐点（10→25→50→100→200→400，每阶 3 分钟）
    LOAD_SCENARIO=s4 LOAD_STAGES=10:180,25:180,50:180,100:180,200:180,400:180 \
        locust -f locustfile.py --headless --html baseline.html --csv baseline

    # 故障注入（配 T6）
    LOAD_SCENARIO=s5 FAULT_RERANKER=fail locust -f locustfile.py --headless -u 10 -r 5 -t 60s

环境变量：
    LOAD_SCENARIO   s1|s2|s3|s4|s5|s6|s7（默认 s4 混合）
    LOAD_HOST       被测后端地址（默认 http://localhost:8002）
    LOAD_STAGES     阶梯定义 "users:seconds,..."（设置后启用 StaircaseShape，忽略 -u/-r）
    LOAD_SPAWN_RATE 每阶 spawn 速率（默认 5/s）
    LOAD_CSV_DIR    明细 CSV 落盘目录（默认当前目录）
    LOAD_TOKEN      直接指定 JWT（否则离线自签 / 走 /auth/login）
"""

from __future__ import annotations

import importlib
import os
import sys
from collections import Counter
from pathlib import Path

from locust import LoadTestShape, events

# 让 lib/ 与 scenarios/ 可被导入（locust 已把本目录加入 sys.path，这里兜底）
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from lib import ingest  # noqa: E402

_SCENARIOS = {
    "s1": ("scenarios.s1_cache_hit", "S1CacheHitUser"),
    "s2": ("scenarios.s2_faq_miss", "S2FaqMissUser"),
    "s3": ("scenarios.s3_task_agent", "S3TaskAgentUser"),
    "s4": ("scenarios.s4_mixed", "S4MixedUser"),
    "s5": ("scenarios.s5_fault_injection", "S5FaultInjectionUser"),
    "s6": ("scenarios.s6_spike", "S6SpikeUser"),
    "s7": ("scenarios.s7_soak", "S7SoakUser"),
}

_SCENARIO_KEY = os.getenv("LOAD_SCENARIO", "s4").strip().lower()
_module_name, _class_name = _SCENARIOS.get(_SCENARIO_KEY, _SCENARIOS["s4"])

# 只把选中的用户类注入本模块命名空间 —— Locust 只 spawn 本模块可见的 User 子类，
# 这样一次只跑一个场景（S4 内部再按 5:3:2 混合）。
# 注意：临时变量必须删掉，否则同一类会以两个属性名出现，触发 Locust「类名重复」校验失败。
_selected = getattr(importlib.import_module(_module_name), _class_name)
globals()[_class_name] = _selected
del _selected

# ===== 阶梯加压 Shape（仅当 LOAD_STAGES 设置时启用）=====
# 注意：Locust 一旦发现 LoadTestShape 就接管 users/spawn/t 参数，且 tick 返回 None
# 表示「测试结束」。因此未设置 LOAD_STAGES 时**不能定义**该 Shape。
if os.getenv("LOAD_STAGES"):
    _stages: list[tuple[int, int]] = []
    for part in os.getenv("LOAD_STAGES", "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        users_s, dur_s = part.split(":", 1)
        _stages.append((int(users_s), int(dur_s)))
    _spawn_rate = float(os.getenv("LOAD_SPAWN_RATE", "5"))

    class StaircaseShape(LoadTestShape):  # type: ignore[no-redef]
        """固定阶梯：每阶稳态保持 >= 3 分钟（等延迟指标收敛）后再升档。"""

        def tick(self):
            run_time = self.get_run_time()
            elapsed = 0.0
            for users, duration in _stages:
                elapsed += duration
                if run_time < elapsed:
                    return (users, _spawn_rate)
            return None  # 所有阶梯跑完 → 结束

        def __repr__(self) -> str:  # pragma: no cover - 仅供日志
            return f"StaircaseShape(stages={_stages}, spawn_rate={_spawn_rate})"


_DEGRADED: Counter[str] = Counter()


@events.test_start.add_listener
def _on_test_start(environment, **_kwargs) -> None:
    ingest.init(os.getenv("LOAD_CSV_DIR"))
    faults = {k: v for k, v in os.environ.items() if k.startswith("FAULT_")}
    print(
        f"[Load] scenario={_SCENARIO_KEY} host={os.getenv('LOAD_HOST', 'http://localhost:8002')} "
        f"stages={os.getenv('LOAD_STAGES', '-')} faults={faults or '无（正常路径）'}",
        flush=True,
    )


@events.request.add_listener
def _on_request(request_type, name, response_time, exception, context=None, **_kwargs) -> None:
    if exception is not None or not context:
        return
    for item in context.get("degraded") or []:
        _DEGRADED[str(item)] += 1


@events.quitting.add_listener
def _on_quitting(environment, **_kwargs) -> None:
    summary = ingest.summary()
    print(f"[Load] 落盘行数（按场景）: {summary}", flush=True)
    if _DEGRADED:
        print(
            "[Load] 降级触发统计（客户端观测）: "
            + ", ".join(f"{k}={v}" for k, v in _DEGRADED.most_common()),
            flush=True,
        )
    # CI 门禁：错误率超阈值 / 有失败请求则标记退出码非 0（配合 profiles/smoke.conf）
    fail_ratio = 0.0
    try:
        stats = environment.stats.total
        if stats.num_requests:
            fail_ratio = stats.num_failures / stats.num_requests
    except Exception:  # noqa: BLE001
        pass
    max_fail = float(os.getenv("LOAD_MAX_FAIL_RATIO", "0.02"))
    if fail_ratio > max_fail:
        print(f"[Load] 错误率 {fail_ratio:.2%} 超过阈值 {max_fail:.2%}，标记失败", flush=True)
        environment.process_exit_code = 1
    ingest.close()
