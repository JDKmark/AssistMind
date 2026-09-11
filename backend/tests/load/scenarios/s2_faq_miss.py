"""S2 FAQ 未命中（完整 RAG 链路：改写 → 检索 → 重排 → CRAG → 生成）。

语料取项目 65 条评估集（语义真实，且压测后可复跑 RAGAS 做质量闸门）。
观察重点：检索/重排耗时、p95。
权重参考 30%。
"""

from __future__ import annotations

from lib.corpus import faq_miss_corpus
from locust import task

from scenarios.base import AssistMindUser

_corpus = faq_miss_corpus()


class S2FaqMissUser(AssistMindUser):
    abstract = False
    scenario_name = "s2_faq_miss"
    weight = 3

    @task
    def rag_qa(self) -> None:
        self.ask(_corpus.next())
