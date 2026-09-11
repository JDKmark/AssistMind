"""S5 故障注入（配 T6）。

由服务端 env ``FAULT_LLM`` / ``FAULT_RERANKER`` / ``FAULT_QDRANT`` 注入故障，
本场景只负责驱动流量并**记录降级是否按预期触发**：

验收四件事（spec 3.6 都要在报告里给证据）：
1. 降级生效：done.degraded 含预期组件（LLM→llm / RERANKER→reranker / QDRANT→qdrant）
2. 有 warning：服务端日志（压测侧用 SSE 正常返回 + degraded 非空间接验证）
3. 错误率受控：error 事件/非 200 占比在阈值内
4. 自动恢复：关闭开关后复跑同一场景，degraded 回落

独立跑（不参与 S4 混合权重）。用法示例：
    FAULT_RERANKER=fail LOAD_SCENARIO=s5 locust -f locustfile.py --headless -u 10 -r 5 -t 60s
"""

from __future__ import annotations

import os
from collections import Counter

from lib.corpus import cache_hit_corpus, faq_miss_corpus
from locust import task

from scenarios.base import AssistMindUser

_miss = faq_miss_corpus(shuffle=False)
_hit = cache_hit_corpus()

# env 声明的期望降级组件（用于判定「故障是否真的触发了降级」）
_EXPECT = {
    "FAULT_LLM": "llm",
    "FAULT_RERANKER": "reranker",
    "FAULT_QDRANT": "qdrant",
}


class S5FaultInjectionUser(AssistMindUser):
    abstract = False
    scenario_name = "s5_fault"
    weight = 1

    def on_start(self) -> None:
        super().on_start()
        self.expected = [
            component
            for env_name, component in _EXPECT.items()
            if os.getenv(env_name, "ok").lower() != "ok"
        ]
        self.observed: Counter[str] = Counter()
        self.missing_degradation = 0
        if not self.expected:
            print(
                "[S5] 未设置任何 FAULT_* 开关（当前为正常路径对照跑）。"
                "注入方式示例：FAULT_RERANKER=fail"
            )

    @task
    def fault_probe(self) -> None:
        # 缓存命中路径会短路检索，测不出 reranker/qdrant 降级 → 只打未命中语料
        result = self.ask(_miss.next(), name="s5_fault:faq_miss")
        for item in result.degraded:
            self.observed[item] += 1
        if self.expected and not any(e in result.degraded for e in self.expected):
            # 允许 LLM 故障时 done.degraded 为 ["llm"]；其余情况必须出现期望组件
            self.missing_degradation += 1
            if self.missing_degradation % 10 == 1:
                print(
                    f"[S5] 降级未按预期出现 期望={self.expected} 实际={result.degraded} "
                    f"（累计 {self.missing_degradation} 次）"
                )

    @task(1)
    def cache_path(self) -> None:
        """对照：缓存命中路径不应受检索层故障影响（F 只影响 miss 路径）。"""
        self.ask(_hit.next(), name="s5_fault:cache_hit")
