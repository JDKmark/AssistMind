"""知识库管理 API 单元测试。

覆盖：
1. 列表：按 doc_id 聚合 chunk 数
2. 列表：Qdrant 不可用时返回 error 字段（不抛 500）
3. 删除：delete_by_doc + BM25 增量 remove_by_doc + 语义缓存失效 + mark_changed 版本广播
4. 删除：doc_id 校验（422）
5. 删除：Qdrant 不可用 / 删除失败（503）；旁路失败（缓存失效/版本广播）不影响删除
6. 重建：scroll_all → get_bm25().build
7. 上传：合法文件入库队（写 uploads 目录 + RQ job）/ 扩展名白名单 / 5MB 上限 / 路径穿越拒绝
8. chunk 明细：scroll_by_doc / 404 / Qdrant 不可用 503
9. 召回测试：透传 retrieve_raw / 空白 query 422 / top_k 越界 422
10. 未认证访问拒绝、角色限制（user 403 / agent、admin 200；admin-only 端点 agent 403）
11. 启停切换：toggle 写 enabled + BM25 mark_changed + 缓存失效 / 404 / 503 / 旁路失败不回滚
12. 单文档重灌：reingest 入队（透传 doc_id/owner）/ 404 / 503

mock 策略：mock app.api.knowledge 中的 get_qdrant / get_bm25 / cache_invalidate /
enqueue_task / retrieve_raw 引用，Qdrant 用 MagicMock 设置 is_connected +
scroll_all/scroll_by_doc/delete_by_doc 返回值，不连真实服务。依赖 get_current_user
用 dependency_overrides 覆盖（autouse fixture，测试结束即清理，避免模块级
override 污染同进程其他测试文件的 /me 等鉴权接口）。
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
    {"doc_id": "doc-1", "title": "产品手册", "source": "mall/manual.md", "category": "mall", "text": "chunk A"},
    {"doc_id": "doc-1", "title": "产品手册", "source": "mall/manual.md", "category": "mall", "text": "chunk B"},
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
    assert by_id["doc-1"]["chunk_count"] == 2
    assert by_id["doc-1"]["title"] == "产品手册"
    assert by_id["doc-1"]["source"] == "mall/manual.md"
    assert by_id["doc-1"]["category"] == "mall"
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


def test_delete_doc_removes_bm25_invalidates_cache_and_broadcasts():
    """删除：delete_by_doc 后 BM25 增量 remove_by_doc（不再 scroll_all 全量重建）、
    语义缓存失效、mark_changed 版本广播，响应形状不变。"""
    qdrant = _mock_qdrant(CHUNKS)
    bm25 = MagicMock()
    with (
        patch("app.api.knowledge.get_qdrant", return_value=qdrant),
        patch("app.api.knowledge.get_bm25", return_value=bm25),
        patch("app.api.knowledge.cache_invalidate", new=AsyncMock()) as mock_invalidate,
    ):
        resp = client.post("/api/v1/knowledge/delete", json={"doc_id": "doc-1"})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "doc_id": "doc-1"}
    qdrant.delete_by_doc.assert_awaited_once_with("doc-1")
    # 增量移除而非全量重建（BM25 一等公民的内存索引原地收缩）
    bm25.remove_by_doc.assert_called_once_with("doc-1")
    bm25.build.assert_not_called()
    qdrant.scroll_all.assert_not_awaited()
    mock_invalidate.assert_awaited_once()
    bm25.mark_changed.assert_called_once()


def test_delete_doc_cache_invalidate_failure_still_200(caplog):
    """删除：旁路步骤（语义缓存失效）失败只 warning，不影响删除结果。"""
    qdrant = _mock_qdrant(CHUNKS)
    bm25 = MagicMock()
    with (
        patch("app.api.knowledge.get_qdrant", return_value=qdrant),
        patch("app.api.knowledge.get_bm25", return_value=bm25),
        patch("app.api.knowledge.cache_invalidate", new=AsyncMock(side_effect=ConnectionError("redis down"))),
    ):
        resp = client.post("/api/v1/knowledge/delete", json={"doc_id": "doc-1"})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "doc_id": "doc-1"}
    qdrant.delete_by_doc.assert_awaited_once_with("doc-1")
    assert any("缓存" in r.message or "invalidate" in r.message.lower() for r in caplog.records)


def test_delete_doc_mark_changed_failure_still_200(caplog):
    """删除：旁路步骤（版本广播 mark_changed）失败只 warning，不影响删除结果。"""
    qdrant = _mock_qdrant(CHUNKS)
    bm25 = MagicMock()
    bm25.mark_changed = MagicMock(side_effect=ConnectionError("redis down"))
    with (
        patch("app.api.knowledge.get_qdrant", return_value=qdrant),
        patch("app.api.knowledge.get_bm25", return_value=bm25),
        patch("app.api.knowledge.cache_invalidate", new=AsyncMock()),
    ):
        resp = client.post("/api/v1/knowledge/delete", json={"doc_id": "doc-1"})
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "doc_id": "doc-1"}
    assert caplog.records


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
        resp = client.post("/api/v1/knowledge/delete", json={"doc_id": "doc-1"})
    assert resp.status_code == 503
    assert "Qdrant" in resp.json()["detail"]


def test_delete_doc_delete_failed_503():
    """删除：delete_by_doc 返回 False（断路器 Open/异常）时返回 503。"""
    qdrant = _mock_qdrant([], deleted=False)
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.post("/api/v1/knowledge/delete", json={"doc_id": "doc-1"})
    assert resp.status_code == 503


# ---------- 3. 重建索引 ----------


def test_rebuild_index_enqueues_rq_job():
    """重建：入队 RQ 任务秒回 job_id（耗时操作异步化，不再同步 build）。"""
    qdrant = _mock_qdrant(CHUNKS)
    job = MagicMock()
    job.id = "job-abc"
    with (
        patch("app.api.knowledge.get_qdrant", return_value=qdrant),
        patch("app.api.knowledge.enqueue_task", return_value=job) as mock_enqueue,
    ):
        resp = client.post("/api/v1/knowledge/rebuild")
    assert resp.status_code == 200
    assert resp.json() == {"job_id": "job-abc", "status": "queued"}
    mock_enqueue.assert_called_once()
    # 入队前不做耗时 scroll/build（秒回的关键）
    qdrant.scroll_all.assert_not_awaited()


def test_rebuild_index_qdrant_unavailable_503():
    """重建：Qdrant 不可用时返回 503 明确错误。"""
    qdrant = _mock_qdrant([], connected=False)
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.post("/api/v1/knowledge/rebuild")
    assert resp.status_code == 503
    assert "Qdrant" in resp.json()["detail"]


def test_rebuild_index_enqueue_redis_down_503():
    """重建：任务队列入队失败（Redis 不可用）→ 503 而非 500（降级路径明确）。"""
    qdrant = _mock_qdrant(CHUNKS)
    with (
        patch("app.api.knowledge.get_qdrant", return_value=qdrant),
        patch(
            "app.api.knowledge.enqueue_task", side_effect=ConnectionError("redis down")
        ),
    ):
        resp = client.post("/api/v1/knowledge/rebuild")
    assert resp.status_code == 503
    assert resp.json()["detail"]


# ---------- 3b. 坏例回流 / 评估入队（RQ 异步任务） ----------


def test_badcase_export_enqueues_rq_job():
    """坏例回流：入队 RQ 任务秒回 job_id（与 rebuild 同模式）。"""
    job = MagicMock()
    job.id = "job-badcase"
    with patch("app.api.knowledge.enqueue_task", return_value=job) as mock_enqueue:
        resp = client.post("/api/v1/knowledge/badcases/export")
    assert resp.status_code == 200
    assert resp.json() == {"job_id": "job-badcase", "status": "queued"}
    mock_enqueue.assert_called_once()


def test_evaluate_enqueues_rq_job():
    """评估运行：入队 RQ 任务秒回 job_id（子进程隔离执行）。"""
    job = MagicMock()
    job.id = "job-eval"
    with patch("app.api.knowledge.enqueue_task", return_value=job) as mock_enqueue:
        resp = client.post("/api/v1/knowledge/evaluate")
    assert resp.status_code == 200
    assert resp.json() == {"job_id": "job-eval", "status": "queued"}
    mock_enqueue.assert_called_once()


async def _agent_user():
    return {"username": "agent1", "role": "agent"}


def test_badcase_export_agent_role_403():
    """坏例回流：仅管理员可入队，agent 返回 403。"""
    app.dependency_overrides[get_current_user] = _agent_user
    try:
        resp = client.post("/api/v1/knowledge/badcases/export")
    finally:
        app.dependency_overrides[get_current_user] = fake_user
    assert resp.status_code == 403


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


# ---------- 6. 文档上传（POST /upload，仅 admin） ----------


def _upload_job_mock():
    job = MagicMock()
    job.id = "job-upload"
    return job


def test_upload_md_enqueues_and_saves_file(tmp_path):
    """上传合法 md：200 {job_id, status:queued}，文件写入 uploads 目录并入队任务。"""
    job = _upload_job_mock()
    with (
        patch("app.api.knowledge.UPLOAD_DIR", str(tmp_path)),
        patch("app.api.knowledge.enqueue_task", return_value=job) as mock_enqueue,
    ):
        resp = client.post(
            "/api/v1/knowledge/upload",
            files={"file": ("退货规则.md", "# 退货规则\n7 天无理由".encode("utf-8"), "text/markdown")},
            data={"category": "业务规则"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"job_id": "job-upload", "status": "queued"}
    # 文件已持久化到 uploads 目录（原始文件留存供追溯）
    saved = tmp_path / "退货规则.md"
    assert saved.exists()
    assert "退货规则" in saved.read_text(encoding="utf-8")
    # 入队的是 ingest_uploaded_document 任务，参数透传 file_path/filename/category
    mock_enqueue.assert_called_once()
    assert mock_enqueue.call_args[0][0] is not None
    kwargs = mock_enqueue.call_args[1]
    assert kwargs["filename"] == "退货规则.md"
    assert kwargs["category"] == "业务规则"
    assert kwargs["owner"] == "testuser"
    assert str(saved) in kwargs["file_path"]


def test_upload_exe_rejected_422(tmp_path):
    """上传 .exe：422 不支持的文件类型，不入队、不写文件。"""
    job = _upload_job_mock()
    with (
        patch("app.api.knowledge.UPLOAD_DIR", str(tmp_path)),
        patch("app.api.knowledge.enqueue_task", return_value=job) as mock_enqueue,
    ):
        resp = client.post(
            "/api/v1/knowledge/upload",
            files={"file": ("data.exe", b"MZ fake binary", "application/octet-stream")},
        )
    assert resp.status_code == 422
    assert "不支持的文件类型" in resp.json()["detail"]
    mock_enqueue.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_upload_oversize_rejected_422(tmp_path):
    """上传 >5MB：422 文件超过上限，不入队。"""
    job = _upload_job_mock()
    big = b"x" * (5 * 1024 * 1024 + 1)
    with (
        patch("app.api.knowledge.UPLOAD_DIR", str(tmp_path)),
        patch("app.api.knowledge.enqueue_task", return_value=job) as mock_enqueue,
    ):
        resp = client.post(
            "/api/v1/knowledge/upload",
            files={"file": ("big.txt", big, "text/plain")},
        )
    assert resp.status_code == 422
    assert "5MB" in resp.json()["detail"]
    mock_enqueue.assert_not_called()


def test_upload_path_traversal_rejected_422(tmp_path):
    """文件名带 ../：422 拒绝（不落盘、不逃逸 uploads 目录）。"""
    job = _upload_job_mock()
    with (
        patch("app.api.knowledge.UPLOAD_DIR", str(tmp_path)),
        patch("app.api.knowledge.enqueue_task", return_value=job) as mock_enqueue,
    ):
        resp = client.post(
            "/api/v1/knowledge/upload",
            files={"file": ("../evil.md", b"# evil", "text/markdown")},
        )
    assert resp.status_code == 422
    mock_enqueue.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_upload_agent_role_403(tmp_path):
    """上传：仅管理员可调用，agent 返回 403。"""
    async def agent_user():
        return {"username": "agent1", "role": "agent"}

    original = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = agent_user
    try:
        with patch("app.api.knowledge.UPLOAD_DIR", str(tmp_path)):
            resp = client.post(
                "/api/v1/knowledge/upload",
                files={"file": ("a.md", b"content", "text/markdown")},
            )
    finally:
        app.dependency_overrides[get_current_user] = original
    assert resp.status_code == 403


# ---------- 7. chunk 明细（GET /{doc_id}/chunks，staff） ----------


DOC_CHUNKS = [
    {"chunk_index": 0, "doc_id": "uploads/退货规则", "title": "退货规则.md", "section_title": "",
     "source": "knowledge/uploads/退货规则.md", "category": "upload", "table_comment": "", "text": "7 天无理由"},
    {"chunk_index": 1, "doc_id": "uploads/退货规则", "title": "退货规则.md", "section_title": "时限",
     "source": "knowledge/uploads/退货规则.md", "category": "upload", "table_comment": "", "text": "超时概不受理"},
]


def test_get_doc_chunks_success():
    """chunk 明细：有数据返回 200 {doc_id, chunks}，chunks 非空。"""
    qdrant = _mock_qdrant(CHUNKS)
    qdrant.scroll_by_doc = AsyncMock(return_value=DOC_CHUNKS)
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        # doc_id 含分隔符（uploads/<stem>），走 path 转换器路由
        resp = client.get("/api/v1/knowledge/uploads/退货规则/chunks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["doc_id"] == "uploads/退货规则"
    assert len(data["chunks"]) == 2
    assert data["chunks"][0]["text"] == "7 天无理由"
    qdrant.scroll_by_doc.assert_awaited_once_with("uploads/退货规则")


def test_get_doc_chunks_not_found_404():
    """chunk 明细：scroll_by_doc 空且聚合列表无该 doc_id → 404。"""
    qdrant = _mock_qdrant(CHUNKS)  # scroll_all 返回 CHUNKS，无 uploads/退货规则
    qdrant.scroll_by_doc = AsyncMock(return_value=[])
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.get("/api/v1/knowledge/not-exist/chunks")
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


def test_get_doc_chunks_exists_in_aggregate_empty_chunks_200():
    """chunk 明细：scroll_by_doc 空但聚合列表有该 doc_id → 200 空数组（不误报 404）。"""
    qdrant = _mock_qdrant([{"doc_id": "ghost", "title": "t", "source": "s", "category": "c", "text": "x"}])
    qdrant.scroll_by_doc = AsyncMock(return_value=[])
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.get("/api/v1/knowledge/ghost/chunks")
    assert resp.status_code == 200
    assert resp.json() == {"doc_id": "ghost", "chunks": []}


def test_get_doc_chunks_qdrant_unavailable_503():
    """chunk 明细：Qdrant 不可用 → 503。"""
    qdrant = _mock_qdrant([], connected=False)
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.get("/api/v1/knowledge/any-doc/chunks")
    assert resp.status_code == 503
    assert "Qdrant" in resp.json()["detail"]


# ---------- 8. 召回测试（POST /search-test，staff） ----------


RETRIEVE_RAW_RESULT = {
    "query": "退货政策是什么",
    "vector": [{"doc_id": "d1", "title": "t", "section_title": "", "text": "x", "score": 0.9}],
    "bm25": [],
    "fused": [{"doc_id": "d1", "title": "t", "section_title": "", "text": "x", "rrf_score": 0.03}],
    "degraded": [],
}


def test_search_test_passes_through_retrieve_raw():
    """召回测试：透传 retrieve_raw 结果（strip 后的 query + role + top_k）。"""
    retrieve_raw = AsyncMock(return_value=RETRIEVE_RAW_RESULT)
    with patch("app.api.knowledge.retrieve_raw", retrieve_raw):
        resp = client.post(
            "/api/v1/knowledge/search-test",
            json={"query": "  退货政策是什么  ", "top_k": 3},
        )
    assert resp.status_code == 200
    assert resp.json() == RETRIEVE_RAW_RESULT
    retrieve_raw.assert_awaited_once_with("退货政策是什么", role="admin", top_k=3)


def test_search_test_blank_query_422():
    """召回测试：query 全空白 → 422。"""
    with patch("app.api.knowledge.retrieve_raw", AsyncMock()) as mock_raw:
        resp = client.post("/api/v1/knowledge/search-test", json={"query": "   "})
    assert resp.status_code == 422
    mock_raw.assert_not_awaited()


def test_search_test_top_k_out_of_range_422():
    """召回测试：top_k=25 越界（1-20）→ 422。"""
    with patch("app.api.knowledge.retrieve_raw", AsyncMock()):
        resp = client.post(
            "/api/v1/knowledge/search-test", json={"query": "q", "top_k": 25}
        )
    assert resp.status_code == 422


def test_search_test_user_role_403():
    """召回测试：user 角色返回 403（staff-only）。"""
    async def user_only():
        return {"username": "u", "role": "user"}

    original = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = user_only
    try:
        resp = client.post("/api/v1/knowledge/search-test", json={"query": "q"})
    finally:
        app.dependency_overrides[get_current_user] = original
    assert resp.status_code == 403


# ---------- 9. 文档启停切换（POST /{doc_id}/toggle，仅 admin） ----------


def _toggle_qdrant(chunks=None, connected=True, payload_set=True):
    qdrant = _mock_qdrant(chunks, connected=connected)
    qdrant.scroll_by_doc = AsyncMock(return_value=DOC_CHUNKS if chunks is None else chunks)
    qdrant.set_payload_by_doc = AsyncMock(return_value=payload_set)
    return qdrant


def test_toggle_doc_success():
    """启停切换：set_payload_by_doc 写 enabled → BM25 mark_changed + 语义缓存失效，返回 {doc_id, enabled}。"""
    qdrant = _toggle_qdrant()
    bm25 = MagicMock()
    with (
        patch("app.api.knowledge.get_qdrant", return_value=qdrant),
        patch("app.api.knowledge.get_bm25", return_value=bm25),
        patch("app.api.knowledge.cache_invalidate", new=AsyncMock()) as mock_invalidate,
    ):
        resp = client.post("/api/v1/knowledge/doc-1/toggle", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json() == {"doc_id": "doc-1", "enabled": False}
    qdrant.set_payload_by_doc.assert_awaited_once_with("doc-1", {"enabled": False})
    # toggle 后 BM25 索引必须重载，停用文档才不会继续被打分
    bm25.mark_changed.assert_called_once()
    # 语义缓存可能引用该文档，必须失效
    mock_invalidate.assert_awaited_once()


def test_toggle_doc_not_found_404():
    """启停切换：scroll_by_doc 空且聚合列表无该 doc_id → 404。"""
    qdrant = _toggle_qdrant(chunks=[])  # scroll_all 返回 CHUNKS，无 ghost
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.post("/api/v1/knowledge/ghost/toggle", json={"enabled": False})
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]
    qdrant.set_payload_by_doc.assert_not_awaited()


def test_toggle_doc_qdrant_unavailable_503():
    """启停切换：Qdrant 不可用 → 503 明确错误。"""
    qdrant = _toggle_qdrant(connected=False)
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.post("/api/v1/knowledge/doc-1/toggle", json={"enabled": True})
    assert resp.status_code == 503
    assert "Qdrant" in resp.json()["detail"]


def test_toggle_doc_set_payload_failed_503():
    """启停切换：set_payload_by_doc 返回 False（断路器 Open/写入异常）→ 503。"""
    qdrant = _toggle_qdrant(payload_set=False)
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.post("/api/v1/knowledge/doc-1/toggle", json={"enabled": False})
    assert resp.status_code == 503
    assert "启停" in resp.json()["detail"]


def test_toggle_doc_bypass_failure_still_200(caplog):
    """启停切换：旁路步骤（mark_changed / 缓存失效）失败只 warning，不回滚切换。"""
    qdrant = _toggle_qdrant()
    bm25 = MagicMock()
    bm25.mark_changed = MagicMock(side_effect=ConnectionError("redis down"))
    with (
        patch("app.api.knowledge.get_qdrant", return_value=qdrant),
        patch("app.api.knowledge.get_bm25", return_value=bm25),
        patch(
            "app.api.knowledge.cache_invalidate",
            new=AsyncMock(side_effect=ConnectionError("redis down")),
        ),
    ):
        resp = client.post("/api/v1/knowledge/doc-1/toggle", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json() == {"doc_id": "doc-1", "enabled": True}
    qdrant.set_payload_by_doc.assert_awaited_once_with("doc-1", {"enabled": True})
    assert caplog.records


def test_toggle_doc_agent_role_403():
    """启停切换：仅管理员可操作，agent（staff）返回 403。"""
    async def agent_user():
        return {"username": "agent1", "role": "agent"}

    original = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = agent_user
    try:
        resp = client.post("/api/v1/knowledge/doc-1/toggle", json={"enabled": False})
    finally:
        app.dependency_overrides[get_current_user] = original
    assert resp.status_code == 403


# ---------- 10. 单文档增量重灌（POST /{doc_id}/reingest，仅 admin） ----------


def _reingest_job_mock():
    job = MagicMock()
    job.id = "job-reingest"
    return job


def test_reingest_doc_enqueues_job():
    """重灌：文档存在 → 入队 reingest_document 任务（透传 doc_id/owner），秒回 job_id。"""
    from app.core.tasks import reingest_document

    qdrant = _toggle_qdrant()  # scroll_by_doc 返回 DOC_CHUNKS（存在）
    job = _reingest_job_mock()
    with (
        patch("app.api.knowledge.get_qdrant", return_value=qdrant),
        patch("app.api.knowledge.enqueue_task", return_value=job) as mock_enqueue,
    ):
        resp = client.post("/api/v1/knowledge/doc-1/reingest")
    assert resp.status_code == 200
    assert resp.json() == {"job_id": "job-reingest", "status": "queued"}
    mock_enqueue.assert_called_once()
    assert mock_enqueue.call_args[0][0] is reingest_document
    kwargs = mock_enqueue.call_args[1]
    assert kwargs["doc_id"] == "doc-1"
    assert kwargs["owner"] == "testuser"
    # 入队前不做耗时重灌动作
    qdrant.set_payload_by_doc.assert_not_awaited()


def test_reingest_doc_not_found_404():
    """重灌：scroll_by_doc 空且聚合列表无该 doc_id → 404，不入队。"""
    qdrant = _toggle_qdrant(chunks=[])
    job = _reingest_job_mock()
    with (
        patch("app.api.knowledge.get_qdrant", return_value=qdrant),
        patch("app.api.knowledge.enqueue_task", return_value=job) as mock_enqueue,
    ):
        resp = client.post("/api/v1/knowledge/ghost/reingest")
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]
    mock_enqueue.assert_not_called()


def test_reingest_doc_qdrant_unavailable_503():
    """重灌：Qdrant 不可用 → 503 明确错误（存在性校验无法进行）。"""
    qdrant = _toggle_qdrant(connected=False)
    with patch("app.api.knowledge.get_qdrant", return_value=qdrant):
        resp = client.post("/api/v1/knowledge/doc-1/reingest")
    assert resp.status_code == 503
    assert "Qdrant" in resp.json()["detail"]


def test_reingest_doc_agent_role_403():
    """重灌：仅管理员可触发，agent（staff）返回 403。"""
    async def agent_user():
        return {"username": "agent1", "role": "agent"}

    original = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = agent_user
    try:
        resp = client.post("/api/v1/knowledge/doc-1/reingest")
    finally:
        app.dependency_overrides[get_current_user] = original
    assert resp.status_code == 403
