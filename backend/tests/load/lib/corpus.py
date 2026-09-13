"""压测语料：复用项目既有评估集 + 高频 FAQ（不另造数据，spec 3.3）。

来源：
- ``app/data/eval_mall_qa.json``（40 条，含 5 条对抗）
- ``app/data/eval_mall_qa.json``（40 条，含 5 条对抗）
- 一组高频 FAQ（退货政策 / 运费谁出 / 优惠券叠加），用于 S1 缓存命中场景

双重价值：① 请求语义真实；② 压测后可顺手复跑 RAGAS 验证「压力下降级是否拖低质量」。
"""

from __future__ import annotations

import itertools
import json
import random
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[3] / "app" / "data"  # backend/app/data

# 高频 FAQ（S1 缓存命中：同一问句重复打，稳态下应命中 L1/L2，免检索免生成）
HIGH_FREQ_FAQ = [
    "退货政策是什么？",
    "退货运费谁承担？",
    "优惠券可以叠加使用吗？",
    "订单多久发货？",
    "支持哪些支付方式？",
]

# Agent 工具链问句（S3）：含明确订单号，触发实体识别 + 业务工具直通
TASK_QUERIES = [
    "查询订单 20260801001 的物流状态",
    "订单 20260802002 什么时候能到？",
    "帮我查一下订单 20260803003 的退款进度",
    "物流到哪了？订单号 20260804004",
    "我要退货，订单 20260805005",
]


def _load_questions(filename: str) -> list[str]:
    path = _DATA_DIR / filename
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 语料缺失时降级为空（不阻塞压测启动）
        return []
    questions: list[str] = []
    items = raw if isinstance(raw, list) else raw.get("items", [])
    for item in items:
        if isinstance(item, str):
            questions.append(item)
        elif isinstance(item, dict):
            q = item.get("question") or item.get("query") or item.get("input")
            if q:
                questions.append(str(q))
    return questions


def evaluation_questions() -> list[str]:
    """65 条评估集全部问题（S2 FAQ 未命中用）。"""
    return _load_questions("eval_mall_qa.json")


class Corpus:
    """语料游标：轮询取问句（可选打散），避免同一进程重复打同一条。"""

    def __init__(self, questions: list[str], *, shuffle: bool = False, seed: int = 20260910):
        self._questions = [q for q in questions if q and q.strip()] or ["你好"]
        if shuffle:
            rng = random.Random(seed)
            rng.shuffle(self._questions)
        self._iter = itertools.cycle(self._questions)

    def next(self) -> str:
        return next(self._iter)

    @property
    def size(self) -> int:
        return len(self._questions)


def faq_miss_corpus(shuffle: bool = True) -> Corpus:
    return Corpus(evaluation_questions(), shuffle=shuffle)


def cache_hit_corpus() -> Corpus:
    return Corpus(HIGH_FREQ_FAQ)


def task_corpus() -> Corpus:
    return Corpus(TASK_QUERIES)
