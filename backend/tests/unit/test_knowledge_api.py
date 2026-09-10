"""知识库管理 API 单元测试。

覆盖：
1. 列表：按 doc_id 聚合 chunk 数
2. 列表：Qdrant 不可用时返回 error 字段（不抛 500）
3. 删除：调 delete_by_doc + 重建 BM25
4. 删除：doc_id 校验（422）
5. 删除：Qdrant 不可用 / 删除失败（503）
6. 重建：scroll_all → get_bm25().build
7. 未认证访问拒绝
8. 列表角色限制（user 403 / agent、admin 200）

mock 策略：mock app.api.knowledge 中的 get_qdrant / get_bm25 引用，
Qdrant 用 MagicMock 设置 is_connected + scroll_all/delete_by_doc 返回值，
不连真实服务。依赖 get_current_user 用 dependency_overrides 覆盖（autouse fixture，
测试结束即清理，避免模块级 override 污染同进程其他测试文件的 /me 等鉴权接口）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app

client = TestClient(app)


async def fake_user():
    return {"username": "testuser", "role": "admin"}


@pytest.fixture(autouse=True)
def _override_auth():
    """每个测试临时用 admin 用户覆盖鉴权依赖，结束后恢复原值（不跨文件污染）。"""
    original = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = fake_user
    yield
    if original is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = original

# 模拟 scroll_all 返回的 chunk（payload 结构对齐 qdrant.py scroll_all）
CHUNKS = [
    {"doc_id": "ops-1", "title": "运维手册", "source": "ops/manual.md", "category": "ops", "text": "chunk A"},
    {"doc_id": "ops-1", "title": "运维手册", "source": "ops/manual.md", "category": "ops", "text": "chunk B"},
    {"doc_id": "mall-1", "title": "商城文档", "source": "mall/guide.md", "category": "mall", "text": "chunk C"},
]


def _mock_qdrant(chunks=None, connected=True, deleted=True):
    qdrant = MagicMock()
    qdrant.is_connected = connected
    qdrant.scroll_all = AsyncMock(return_value=chunks if chunks is not None else [])
    qdrant.delete_by_doc = AsyncMock(return_value=deleted)
    return qdrant


# ---------- 1. 列表 ----------


def test_list_docs_aggregates_by_doc_id():
    """列表：按 doc_id 聚合 chunk 数，返回 {docs, total}。"""
    with patch("app.api.knowledge.get_qdrant", return_value=_mock_qdrant(CHUNKS)):
        resp = client.get("/api/v1/knowledge/list")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert "error" not in data
    by_id = {d["doc_id"]: d for d in data["docs"]}
    assert by_id["ops-1"]["chunk_count"] == 2
    assert by_id["ops-1"]["title"] == "运维手册"
    assert by_id["ops-1"]["source"] == "ops/manual.md"
    assert by_id["ops-1"]["category"] == "ops"
    assert by_id["mall-1"]["chunk_count"] == 1
    assert by_id["mall-1"]["category"] == "mall"


def test_list_docs_empty():
    """列表：Qdrant 无数据时返回空列表。"""
    with patch("app.api.knowledge.get_qdrant", return_value=_mock_qdrant([])):
        resp = client.get("/api/v1/knowledge/list")
    assert resp.status_code == 200
    data = resp.json()
    assert data["docs"] == []
    assert data["total"] == 0


def test_list_docs_qdrant_unavailable_returns_error_field():
    """列表：Qdrant 不可用时返回 200 + error 字段（不抛 500）。"""
    with patch("app.api.knowledge.get_qdrant", return_value=_mock_qdrant([], connected=False)):
        resp = client.get("/api/v1/knowledge/list")
    assert resp.status_code == 200
    data = resp.json()
    assert data["docs"] == []
    assert data["total"] == 0
    assert data["error"]


# ---------- 2. 删除 ----------


def test_delete_doc_deletes_and_rebuilds_bm25():
    """删除：调 delete_by_doc(doc_id) 并用剩余 chunk 重建 BM25。"""
    qdrant = _mock_qdrant(CHUNKS)
    bm25 = MagicMock()
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant), patch(
        "app.api.knowledge.get_bm25", return_value=bm25
    ):
        resp = client.post("/api/v1/knowledge/delete", json={"doc_id": "ops-1"})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "doc_id": "ops-1"}
    qdrant.delete_by_doc.assert_awaited_once_with("ops-1")
    # 删除后以 scroll_all 全量结果重建 BM25 索引
    qdrant.scroll_all.assert_awaited_once()
    bm25.build.assert_called_once()
    assert bm25.build.call_args[0][0] == CHUNKS


def test_delete_doc_validation_failed():
    """删除：doc_id 为空时 422，不触发 Qdrant 调用。"""
    qdrant = _mock_qdrant()
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.post("/api/v1/knowledge/delete", json={"doc_id": ""})
    assert resp.status_code == 422
    qdrant.delete_by_doc.assert_not_awaited()


def test_delete_doc_qdrant_unavailable_503():
    """删除：Qdrant 不可用时返回 503 明确错误。"""
    qdrant = _mock_qdrant([], connected=False)
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.post("/api/v1/knowledge/delete", json={"doc_id": "ops-1"})
    assert resp.status_code == 503
    assert "Qdrant" in resp.json()["detail"]


def test_delete_doc_delete_failed_503():
    """删除：delete_by_doc 返回 False（断路器 Open/异常）时返回 503。"""
    qdrant = _mock_qdrant([], deleted=False)
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.post("/api/v1/knowledge/delete", json={"doc_id": "ops-1"})
    assert resp.status_code == 503


# ---------- 3. 重建索引 ----------


def test_rebuild_index_builds_bm25():
    """重建：scroll_all 全量结果重建 BM25，返回 chunk 数。"""
    qdrant = _mock_qdrant(CHUNKS)
    bm25 = MagicMock()
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant), patch(
        "app.api.knowledge.get_bm25", return_value=bm25
    ):
        resp = client.post("/api/v1/knowledge/rebuild")
    assert resp.status_code == 200
    data = resp.json()
    assert data["rebuilt"] is True
    assert data["chunks"] == len(CHUNKS)
    qdrant.scroll_all.assert_awaited_once()
    bm25.build.assert_called_once()
    assert bm25.build.call_args[0][0] == CHUNKS


def test_rebuild_index_qdrant_unavailable_503():
    """重建：Qdrant 不可用时返回 503 明确错误。"""
    qdrant = _mock_qdrant([], connected=False)
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.post("/api/v1/knowledge/rebuild")
    assert resp.status_code == 503
    assert "Qdrant" in resp.json()["detail"]


# ---------- 4. 认证 ----------


def test_knowledge_requires_auth():
    """未认证访问被拒绝（清除 override 后验证）。"""
    app.dependency_overrides.pop(get_current_user, None)
    try:
        resp = client.get("/api/v1/knowledge/list")
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides[get_current_user] = fake_user


# ---------- 5. 列表角色限制 ----------


def test_list_docs_user_forbidden():
    """user 角色调列表：403（仅 agent/admin 可访问）。"""
    async def user_only():
        return {"username": "testuser", "role": "user"}

    original = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = user_only
    try:
        resp = client.get("/api/v1/knowledge/list")
    finally:
        app.dependency_overrides[get_current_user] = original
    assert resp.status_code == 403
    assert resp.json()["detail"] == "需要客服或管理员权限"


def test_list_docs_agent_allowed():
    """agent 角色调列表：200。"""
    async def agent_user():
        return {"username": "agent1", "role": "agent"}

    original = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = agent_user
    try:
        with patch("app.api.knowledge.get_qdrant", return_value=_mock_qdrant(CHUNKS)):
            resp = client.get("/api/v1/knowledge/list")
    finally:
        app.dependency_overrides[get_current_user] = original
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_list_docs_admin_allowed():
    """admin 角色调列表：200。"""
    async def admin_user():
        return {"username": "admin1", "role": "admin"}

    original = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = admin_user
    try:
        with patch("app.api.knowledge.get_qdrant", return_value=_mock_qdrant(CHUNKS)):
            resp = client.get("/api/v1/knowledge/list")
    finally:
        app.dependency_overrides[get_current_user] = original
    assert resp.status_code == 200
    assert resp.json()["total"] == 2
