"""反馈 API 单元测试。

覆盖：
1. 提交反馈成功
2. 评分越界（score=0）-> 422（Pydantic Field(ge=1) 校验）
3. 评分越界（score=6）-> 422（Pydantic Field(le=5) 校验）
4. 评分正常边界（1 和 5）-> 200

mock 策略：mock app.api.feedback.submit_feedback，不连真实 DB。
依赖 get_current_user 用 dependency_overrides 覆盖为 fake_user。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app

client = TestClient(app)


# 覆盖 get_current_user 依赖
async def fake_user():
    return {"username": "testuser", "role": "user"}


app.dependency_overrides[get_current_user] = fake_user


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
