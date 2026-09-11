"""jobs API 单元测试（P0：RQ 异步任务状态查询 + rebuild 入队改造）。

覆盖：
- rebuild 端点：改为入队返回 job_id（BREAKING：不再同步返回 rebuilt/chunks）
- rebuild 端点：Qdrant 不可用仍 503（快速失败不浪费队列）
- GET /api/v1/jobs/{id}：状态/结果/错误形状；未知 job 404；user 角色 403

mock 策略：monkeypatch enqueue_task / fetch_job（app.api 命名空间），
不连真实 Redis / Qdrant。鉴权用 dependency_overrides（autouse fixture 同其他 API 测试）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app

client = TestClient(app)


async def fake_user():
    return {"user_id": "uid-agent", "username": "agent", "role": "agent"}


async def fake_admin():
    return {"user_id": "uid-admin", "username": "admin", "role": "admin"}


@pytest.fixture(autouse=True)
def _override_auth():
    """每个测试临时用 agent 覆盖鉴权，结束后恢复（不跨文件污染）。"""
    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = fake_user
    yield
    if original is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = original


def _switch(role: str):
    app.dependency_overrides[get_current_user] = fake_admin if role == "admin" else fake_user


# ---------- rebuild 改为入队（require_admin） ----------


def test_rebuild_enqueues_and_returns_job_id():
    """rebuild：入队任务并秒回 job_id，不再同步执行。"""
    job = MagicMock()
    job.id = "job-123"
    qdrant = MagicMock()
    qdrant.is_connected = True
    original = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = fake_admin
    try:
        with (
            patch("app.api.knowledge.get_qdrant", return_value=qdrant),
            patch("app.api.knowledge.enqueue_task", return_value=job) as mock_enqueue,
        ):
            resp = client.post("/api/v1/knowledge/rebuild")
    finally:
        app.dependency_overrides[get_current_user] = original
    assert resp.status_code == 200
    assert resp.json() == {"job_id": "job-123", "status": "queued"}
    mock_enqueue.assert_called_once()
    enqueued_func = mock_enqueue.call_args[0][0]
    assert enqueued_func.__name__ == "rebuild_knowledge_base"


def test_rebuild_qdrant_unavailable_503_without_enqueue():
    """rebuild：Qdrant 不可用仍 503，不入队。"""
    qdrant = MagicMock()
    qdrant.is_connected = False
    original = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = fake_admin
    try:
        with (
            patch("app.api.knowledge.get_qdrant", return_value=qdrant),
            patch("app.api.knowledge.enqueue_task") as mock_enqueue,
        ):
            resp = client.post("/api/v1/knowledge/rebuild")
    finally:
        app.dependency_overrides[get_current_user] = original
    assert resp.status_code == 503
    assert "Qdrant" in resp.json()["detail"]
    mock_enqueue.assert_not_called()


def test_rebuild_agent_role_403():
    """agent 角色无权触发重建（维持 require_admin 语义）。"""
    resp = client.post("/api/v1/knowledge/rebuild")
    assert resp.status_code == 403


# ---------- jobs 状态查询 ----------


def _owned_job(job_id: str, owner: str = "agent") -> MagicMock:
    """构造带 owner meta 的 job mock（默认 owner=agent 与默认查看者一致）。"""
    job = MagicMock()
    job.id = job_id
    job.get_meta.return_value = {"owner": owner}
    job.exc_info = None
    return job


def test_get_job_running_shape():
    """running 状态：status + job_id，无 result。"""
    job = _owned_job("job-run")
    job.get_status.return_value = "started"
    job.return_value.return_value = None
    with patch("app.api.jobs.fetch_job", return_value=job):
        resp = client.get("/api/v1/jobs/job-run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == "job-run"
    assert data["status"] == "started"
    assert "result" not in data
    assert "error" not in data


def test_get_job_finished_with_result():
    """finished 状态：附带任务返回值。"""
    job = _owned_job("job-fin")
    job.get_status.return_value = "finished"
    job.return_value.return_value = {"rebuilt": True, "chunks": 42}
    with patch("app.api.jobs.fetch_job", return_value=job):
        resp = client.get("/api/v1/jobs/job-fin")
    assert resp.status_code == 200
    assert resp.json()["result"] == {"rebuilt": True, "chunks": 42}


def test_get_job_failed_with_error():
    """failed 状态：附带错误摘要，不暴露完整栈。"""
    job = _owned_job("job-fail")
    job.get_status.return_value = "failed"
    job.return_value.return_value = None
    job.exc_info = "ValueError: boom"
    with patch("app.api.jobs.fetch_job", return_value=job):
        resp = client.get("/api/v1/jobs/job-fail")
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    assert resp.json()["error"] == "ValueError: boom"


def test_get_job_failed_error_strips_traceback_paths():
    """failed 状态：exc_info 为完整 traceback 时仅透出末行（异常类型+消息），
    不暴露内部栈帧文件路径。"""
    job = _owned_job("job-tb")
    job.get_status.return_value = "failed"
    job.return_value.return_value = None
    job.exc_info = (
        "Traceback (most recent call last):\n"
        '  File "app/core/tasks/__init__.py", line 97, in rebuild_knowledge_base\n'
        "    chunks = await qdrant.scroll_all()\n"
        "RuntimeError: postgres down"
    )
    with patch("app.api.jobs.fetch_job", return_value=job):
        resp = client.get("/api/v1/jobs/job-tb")
    assert resp.status_code == 200
    assert resp.json()["error"] == "RuntimeError: postgres down"
    assert "Traceback" not in resp.json()["error"]
    assert "File" not in resp.json()["error"]


def test_get_job_other_owner_agent_404():
    """agent 查他人入队的 job：404（result 含评估 stdout，防横向越权读）。"""
    job = _owned_job("job-other", owner="admin")
    with patch("app.api.jobs.fetch_job", return_value=job):
        resp = client.get("/api/v1/jobs/job-other")
    assert resp.status_code == 404


def test_get_job_admin_reads_any_job():
    """admin 可查任意 job（入队端点本身仅 admin 可用）。"""
    _switch("admin")
    job = _owned_job("job-admin-view", owner="someone-else")
    job.get_status.return_value = "finished"
    job.return_value.return_value = {"ok": True}
    try:
        with patch("app.api.jobs.fetch_job", return_value=job):
            resp = client.get("/api/v1/jobs/job-admin-view")
    finally:
        _switch("agent")
    assert resp.status_code == 200
    assert resp.json()["result"] == {"ok": True}


def test_get_job_legacy_no_owner_admin_only():
    """无 owner meta 的历史 job：agent 404、admin 放行（防枚举统一形状）。"""
    job = MagicMock()
    job.id = "job-legacy"
    job.get_meta.return_value = {}
    job.get_status.return_value = "started"
    with patch("app.api.jobs.fetch_job", return_value=job):
        resp_agent = client.get("/api/v1/jobs/job-legacy")
    assert resp_agent.status_code == 404
    _switch("admin")
    try:
        with patch("app.api.jobs.fetch_job", return_value=job):
            resp_admin = client.get("/api/v1/jobs/job-legacy")
    finally:
        _switch("agent")
    assert resp_admin.status_code == 200


def test_get_job_not_found_404():
    """未知 job：404。"""
    with patch("app.api.jobs.fetch_job", return_value=None):
        resp = client.get("/api/v1/jobs/nope")
    assert resp.status_code == 404


def test_get_job_user_role_403():
    """user 角色无权查询任务状态（与 rebuild 的 staff 权限一致）。"""
    async def user_only():
        return {"user_id": "u1", "username": "user1", "role": "user"}

    original = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = user_only
    try:
        resp = client.get("/api/v1/jobs/anything")
    finally:
        app.dependency_overrides[get_current_user] = original
    assert resp.status_code == 403


def test_get_job_requires_auth():
    """未认证：401/403。"""
    app.dependency_overrides.pop(get_current_user, None)
    try:
        resp = client.get("/api/v1/jobs/x")
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides[get_current_user] = fake_user
