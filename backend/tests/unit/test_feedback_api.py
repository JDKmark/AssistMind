"""反馈 API 单元测试。

覆盖：
1. 提交反馈成功
2. 评分越界（score=0）-> 422（Pydantic Field(ge=1) 校验）
3. 评分越界（score=6）-> 422（Pydantic Field(le=5) 校验）
4. 评分正常边界（1 和 5）-> 200
5. RAG Bad Case 闭环字段透传（conversation_id/trace_id/query/answer/sources/intent）
6. GET 查询：非 admin -> 403；admin -> 过滤/分页参数透传

mock 策略：mock app.api.feedback.submit_feedback / list_feedback，不连真实 DB。
依赖 get_current_user 用 dependency_overrides 覆盖为 fake_user（autouse fixture，
结束后恢复原值，不跨文件污染其他测试的鉴权接口）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app

client = TestClient(app)


# 覆盖 get_current_user 依赖
async def fake_user():
    return {"username": "testuser", "role": "user"}


async def fake_admin():
    return {"username": "admin", "role": "admin"}


@pytest.fixture(autouse=True)
def _override_auth():
    """每个测试临时用 user 用户覆盖鉴权依赖，结束后恢复原值（不跨文件污染）。"""
    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = fake_user
    yield
    if original is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = original


# ---------- 1. 提交反馈成功 ----------


@patch("app.api.feedback.submit_feedback", new_callable=AsyncMock)
def test_submit_feedback_success(mock_submit):
    """提交反馈成功：mock submit_feedback 返回 {feedback_id, created}，POST / 验证 200。"""
    mock_submit.return_value = {"feedback_id": "FB-1", "created": True}
    resp = client.post(
        "/api/v1/feedback/",
        json={"score": 5, "comment": "很满意", "ticket_id": "TK-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feedback_id"] == "FB-1"
    assert data["created"] is True
    mock_submit.assert_awaited_once()
    # 验证 user_id 透传
    _, kwargs = mock_submit.call_args
    assert kwargs.get("user_id") == "testuser"


# ---------- 2. 评分越界 score=0 ----------


@patch("app.api.feedback.submit_feedback", new_callable=AsyncMock)
def test_submit_feedback_score_too_low(mock_submit):
    """score=0：Pydantic Field(ge=1) 校验返回 422，submit_feedback 不应被调用。"""
    resp = client.post(
        "/api/v1/feedback/",
        json={"score": 0, "comment": ""},
    )
    assert resp.status_code == 422
    mock_submit.assert_not_awaited()


# ---------- 3. 评分越界 score=6 ----------


@patch("app.api.feedback.submit_feedback", new_callable=AsyncMock)
def test_submit_feedback_score_too_high(mock_submit):
    """score=6：Pydantic Field(le=5) 校验返回 422，submit_feedback 不应被调用。"""
    resp = client.post(
        "/api/v1/feedback/",
        json={"score": 6, "comment": ""},
    )
    assert resp.status_code == 422
    mock_submit.assert_not_awaited()


# ---------- 4. 评分正常边界 ----------


@patch("app.api.feedback.submit_feedback", new_callable=AsyncMock)
def test_submit_feedback_score_boundary_low(mock_submit):
    """score=1（下界）：验证 200。"""
    mock_submit.return_value = {"feedback_id": "FB-2", "created": True}
    resp = client.post(
        "/api/v1/feedback/",
        json={"score": 1, "comment": "不满意"},
    )
    assert resp.status_code == 200


@patch("app.api.feedback.submit_feedback", new_callable=AsyncMock)
def test_submit_feedback_score_boundary_high(mock_submit):
    """score=5（上界）：验证 200。"""
    mock_submit.return_value = {"feedback_id": "FB-3", "created": True}
    resp = client.post(
        "/api/v1/feedback/",
        json={"score": 5, "comment": "非常满意"},
    )
    assert resp.status_code == 200


# ---------- 5. RAG Bad Case 闭环字段透传 ----------


@patch("app.api.feedback.submit_feedback", new_callable=AsyncMock)
def test_submit_feedback_passes_badcase_fields(mock_submit):
    """提交反馈携带会话/trace/回答快照：完整透传到 submit_feedback。"""
    mock_submit.return_value = {"feedback_id": "FB-4", "created": True}
    sources = [
        {"title": "商品退货规则", "source": "knowledge/mall/business.md", "doc_id": "mall/business.md"}
    ]
    resp = client.post(
        "/api/v1/feedback/",
        json={
            "score": 2,
            "comment": "没答到点上",
            "conversation_id": "conv-abc",
            "trace_id": "trace-xyz",
            "query": "退货多久到账？",
            "answer": "48 小时内。",
            "sources": sources,
            "intent": "faq",
            "crag_action": "rewrite_retry",
            "degraded": ["reranker"],
        },
    )
    assert resp.status_code == 200
    _, kwargs = mock_submit.call_args
    assert kwargs["conversation_id"] == "conv-abc"
    assert kwargs["trace_id"] == "trace-xyz"
    assert kwargs["query"] == "退货多久到账？"
    assert kwargs["answer"] == "48 小时内。"
    assert kwargs["sources"] == sources
    assert kwargs["intent"] == "faq"
    assert kwargs["crag_action"] == "rewrite_retry"
    assert kwargs["degraded"] == ["reranker"]


# ---------- 6. GET 查询：权限与过滤 ----------


@patch("app.api.feedback.list_feedback", new_callable=AsyncMock)
def test_list_feedback_requires_admin(mock_list):
    """非 admin（user 角色）：GET /api/v1/feedback/ 返回 403，list_feedback 不被调用。"""
    resp = client.get("/api/v1/feedback/")
    assert resp.status_code == 403
    mock_list.assert_not_awaited()


@patch("app.api.feedback.list_feedback", new_callable=AsyncMock)
def test_list_feedback_admin_filters(mock_list):
    """admin：GET 支持 score/exported/分页过滤，参数透传到 list_feedback；响应附 langfuse_host。"""
    mock_list.return_value = {
        "total": 1,
        "items": [{"id": "f1", "score": 2, "exported": False}],
    }
    app.dependency_overrides[get_current_user] = fake_admin
    try:
        resp = client.get(
            "/api/v1/feedback/?score=2&exported=false&page=2&page_size=10"
        )
    finally:
        # 恢复 autouse fixture 的 user 覆盖
        app.dependency_overrides[get_current_user] = fake_user

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == "f1"
    # 追溯跳转：响应附 Langfuse host（trace_id 非空且已配置时前端拼链接）
    assert "langfuse_host" in data
    mock_list.assert_awaited_once()
    _, kwargs = mock_list.call_args
    assert kwargs["score"] == 2
    assert kwargs["exported"] is False
    assert kwargs["page"] == 2
    assert kwargs["page_size"] == 10
