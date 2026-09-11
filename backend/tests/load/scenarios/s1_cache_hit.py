"""S1 高频重复问句（缓存命中）。

观察重点：L1/L2 命中率、TTFT、能否免检索免生成。
稳态下应看到 from_cache=L1/L2 占比上升、ttft_ms ≈ 0（无 delta 时为 -1）。
权重参考 50%。
"""

from __future__ import annotations

from lib.corpus import cache_hit_corpus
from locust import task

from scenarios.base import AssistMindUser

_corpus = cache_hit_corpus()


class S1CacheHitUser(AssistMindUser):
    abstract = False
    scenario_name = "s1_cache_hit"
    weight = 5

    @task
    def repeat_faq(self) -> None:
        self.ask(_corpus.next())
