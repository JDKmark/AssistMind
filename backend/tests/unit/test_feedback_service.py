"""反馈服务单元测试（不连真实 PostgreSQL）。

覆盖：
1. submit_feedback 写入 Bad Case 闭环字段（sources JSON 序列化）
2. submit_feedback 忽略无法序列化的 sources（降级不抛异常）
3. list_feedback 按 score/exported 过滤并解析 sources JSON
4. mark_exported 幂等标记（空列表直接返回 0）

mock 策略：patch feedback_service.async_session 为内存 fake 上下文管理器。
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

from app.core.feedback_service import (
    list_feedback,
    mark_exported,
    submit_feedback,
)
from app.models.feedback import Feedback


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """可编程 fake session：add/commit/refresh/scalar/execute。"""

    def __init__(self, total: int = 0, rows=None):
        self.total = total
        self.rows = rows or []
        self.added = None
        self.updated_ids: list[str] = []
        self.rowcount = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def add(self, obj):
        self.added = obj

    async def commit(self):
        pass

    async def refresh(self, obj=None):
        pass

    async def scalar(self, _stmt):
        return self.total

    async def execute(self, _stmt):
        if self.rows is not None:
            return _FakeResult(self.rows)
        return _FakeResult([])


class _FakeAsyncSessionFactory:
    def __init__(self, fake):
        self._fake = fake

    def __call__(self):
        return self._fake


def _sample_feedback() -> Feedback:
    fb = Feedback(
        id="f1",
        score=2,
        comment="没答到点上",
        user_id="u1",
        conversation_id="conv-1",
        trace_id="trace-1",
        query="退货多久到账？",
        answer="48 小时内。",
        sources=json.dumps(
            [{"title": "t", "source": "s", "doc_id": "d"}], ensure_ascii=False
        ),
        intent="faq",
        crag_action="rewrite_retry",
        degraded=json.dumps(["reranker"], ensure_ascii=False),
        exported=False,
    )
    fb.created_at = datetime(2026, 8, 1, 10, 0, 0)
    return fb


# ---------- 1. submit_feedback 闭环字段 ----------


async def test_submit_feedback_badcase_fields_serialized():
    """闭环字段写入 Feedback，sources/degraded 序列化为 JSON 文本。"""
    fake = _FakeSession()
    with patch(
        "app.core.feedback_service.async_session",
        new=_FakeAsyncSessionFactory(fake),
    ):
        result = await submit_feedback(
            score=2,
            comment="没答到点上",
            conversation_id="conv-1",
            trace_id="trace-1",
            query="退货多久到账？",
            answer="48 小时内。",
            sources=[{"title": "t", "source": "s", "doc_id": "d"}],
            intent="faq",
            crag_action="rewrite_retry",
            degraded=["reranker"],
        )

    assert result["created"] is True
    fb = fake.added
    assert fb.conversation_id == "conv-1"
    assert fb.trace_id == "trace-1"
    assert fb.query == "退货多久到账？"
    assert fb.answer == "48 小时内。"
    assert fb.intent == "faq"
    assert fb.crag_action == "rewrite_retry"
    assert json.loads(fb.sources) == [{"title": "t", "source": "s", "doc_id": "d"}]
    assert json.loads(fb.degraded) == ["reranker"]


async def test_submit_feedback_unserializable_sources_downgrades():
    """sources 无法序列化：记 warning 并降级为 None，不抛异常。"""
    fake = _FakeSession()
    with patch(
        "app.core.feedback_service.async_session",
        new=_FakeAsyncSessionFactory(fake),
    ):
        result = await submit_feedback(score=4, sources=[{"bad": object()}])

    assert result["created"] is True
    assert fake.added.sources is None


# ---------- 2. list_feedback 过滤与解析 ----------


async def test_list_feedback_returns_parsed_items():
    """按 score/exported 过滤（fake 未校验 SQL），sources JSON 反序列化回列表。"""
    fake = _FakeSession(total=1, rows=[_sample_feedback()])
    with patch(
        "app.core.feedback_service.async_session",
        new=_FakeAsyncSessionFactory(fake),
    ):
        res = await list_feedback(score=2, exported=False, page=1, page_size=10)

    assert res["total"] == 1
    item = res["items"][0]
    assert item["id"] == "f1"
    assert item["query"] == "退货多久到账？"
    assert item["conversation_id"] == "conv-1"
    assert item["trace_id"] == "trace-1"
    assert item["sources"] == [{"title": "t", "source": "s", "doc_id": "d"}]
    assert item["exported"] is False
    # 追溯快照：crag_action 与降级项反序列化
    assert item["crag_action"] == "rewrite_retry"
    assert item["degraded"] == ["reranker"]


# ---------- 3. mark_exported 幂等 ----------


async def test_mark_exported_empty_noop():
    """空 id 列表：直接返回 0，不触碰 session。"""
    assert await mark_exported([]) == 0
