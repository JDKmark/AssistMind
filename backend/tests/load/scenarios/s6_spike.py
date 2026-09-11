"""S6 尖峰（spike）。

形态：短时间内突发 ×5 负载，观察是否触发限流（429）、是否出现排队雪崩。

实现：本用户 ``wait_time`` 极短（近似无间隔突发），尖峰本身由 profile
（``profiles/stress.conf``，阶梯上升到超载区 / 或 ``-u`` 瞬时拉满）制造。
观察重点：429 占比、503 占比、SSE 中断率、恢复时间。
"""

from __future__ import annotations

from lib.corpus import cache_hit_corpus, faq_miss_corpus
from locust import task

from scenarios.base import AssistMindUser

_hit = cache_hit_corpus()
_miss = faq_miss_corpus()


class S6SpikeUser(AssistMindUser):
    abstract = False
    scenario_name = "s6_spike"
    weight = 1

    def wait_time(self) -> float:
        """突发：几乎不等待（尖峰定义），压力由 users 数量制造。"""
        return 0.0

    @task(3)
    def burst_miss(self) -> None:
        self.ask(_miss.next(), name="s6_spike:faq_miss")

    @task(1)
    def burst_hit(self) -> None:
        self.ask(_hit.next(), name="s6_spike:cache_hit")
