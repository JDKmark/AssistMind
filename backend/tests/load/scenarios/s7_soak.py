"""S7 浸泡（soak，稳态 30–60 min）。

观察重点：内存 / 连接 / 缓存泄漏 —— 稳态下 p95、错误率、degraded 占比应保持平坦，
若随时长单调上升则存在泄漏（配合 Prometheus 看 RSS、DB 连接 active/waiting、
Redis 连接数）。

用法：
    LOAD_SCENARIO=s7 locust -f locustfile.py --headless -u 20 -r 2 -t 45m --html soak.html
"""

from __future__ import annotations

import os

from lib.corpus import cache_hit_corpus, faq_miss_corpus, task_corpus
from locust import task

from scenarios.base import AssistMindUser

_hit = cache_hit_corpus()
_miss = faq_miss_corpus()
_task = task_corpus()


class S7SoakUser(AssistMindUser):
    abstract = False
    scenario_name = "s7_soak"
    weight = 1
    # 稳态：慢速持续打（不要突发），便于观察时间维度的劣化趋势
    wait_time = staticmethod(lambda: float(os.getenv("LOAD_SOAK_WAIT", "2")))

    @task(5)
    def steady_hit(self) -> None:
        self.ask(_hit.next(), name="s7_soak:cache_hit")

    @task(3)
    def steady_miss(self) -> None:
        self.ask(_miss.next(), name="s7_soak:faq_miss")

    @task(2)
    def steady_task(self) -> None:
        self.ask(_task.next(), name="s7_soak:task")
