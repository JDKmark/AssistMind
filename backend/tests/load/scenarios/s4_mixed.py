"""S4 混合流量（默认场景）：S1:S2:S3 = 5:3:2。

Locust 的 ``tasks`` 权重即为场景配比；观察重点：**整体 SLO**。
基线/阶梯加压默认跑本场景（``LOAD_SCENARIO=s4``）。
"""

from __future__ import annotations

from lib.corpus import cache_hit_corpus, faq_miss_corpus, task_corpus
from locust import task

from scenarios.base import AssistMindUser

_hit = cache_hit_corpus()
_miss = faq_miss_corpus()
_task = task_corpus()

_FOLLOW_UP = ["物流到哪了？", "大概什么时候能到？"]


class S4MixedUser(AssistMindUser):
    abstract = False
    scenario_name = "s4_mixed"
    weight = 1
    _turn = 0

    @task(5)
    def cache_hit(self) -> None:
        self.ask(_hit.next(), name="s4_mixed:cache_hit")

    @task(3)
    def faq_miss(self) -> None:
        self.ask(_miss.next(), name="s4_mixed:faq_miss")

    @task(2)
    def task_agent(self) -> None:
        first = _task.next()
        turn = getattr(self, "_turn", 0)
        self._turn = turn + 1
        if turn % 2 == 0:
            history = [
                {"role": "user", "content": first},
                {"role": "assistant", "content": "已为您查询到该订单的信息。"},
            ]
            self.ask(
                _FOLLOW_UP[turn % len(_FOLLOW_UP)],
                name="s4_mixed:task_multi",
                history=history,
            )
        else:
            self.ask(first, name="s4_mixed:task")
