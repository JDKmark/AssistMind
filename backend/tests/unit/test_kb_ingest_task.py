"""上传入库 / 单文档重灌任务单元测试（共用 _ingest_from_file）。

覆盖：
1. md 成功入库：ingested=True、doc_id=uploads/<stem>、delete 先于 upsert、
   全量重建 BM25 + mark_changed + 语义缓存失效
2. 文件不存在 / 空文本 / 解析失败：error dict + warning（不抛栈）
3. Qdrant 不可用：error dict、不触发 delete/BM25
4. embedding 失败：error dict 且 delete_by_doc 未调用（旧版本保留）
5. reingest_document：resolve 命中 → 元数据复用（category/enabled 透传）、
   源文件缺失 error、Qdrant 不可用 error
6. resolve_source_path：uploads 前缀 / mall 相对路径 / 多扩展名 mtime 最新 / 无命中
7. 函数模块级可被 RQ 引用
8. 端到端：fakeredis + SimpleWorker(burst=True)

mock 策略：embed_sync / qdrant / bm25 / cache_invalidate 全 mock（chunk_text
为纯函数跑真实实现），不连外部服务（规范同 test_tasks.py）。
"""

from __future__ import annotations

import inspect
import os
import pickle
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fakeredis import FakeRedis
from rq import Queue, SimpleWorker

import app.core.tasks as tasks


def _embed_ok(texts):
    """mock embedding：按输入数量返回 8 维向量。"""
    return [[0.1] * 8 for _ in texts]


def _make_qdrant(connected=False, connect_ok=True, scroll_chunks=None, existing=None):
    """构造 mock qdrant；connect_ok=False 模拟连接失败（is_connected 保持 False）。"""
    qdrant = MagicMock()
    qdrant.is_connected = connected
    if connect_ok:
        qdrant.connect = AsyncMock(side_effect=lambda: setattr(qdrant, "is_connected", True))
    else:
        qdrant.connect = AsyncMock()
    qdrant.scroll_all = AsyncMock(return_value=scroll_chunks if scroll_chunks is not None else [])
    qdrant.scroll_by_doc = AsyncMock(return_value=existing or [])
    qdrant.delete_by_doc = AsyncMock(return_value=True)
    qdrant.upsert = AsyncMock(return_value=True)
    return qdrant


def _track_write_order(qdrant):
    """记录 delete_by_doc → upsert 的调用顺序（重灌先删后写断言用）。"""
    order = []
    qdrant.delete_by_doc = AsyncMock(side_effect=lambda doc_id: order.append("delete") or True)
    qdrant.upsert = AsyncMock(
        side_effect=lambda chunks, embeddings: order.append("upsert") or True
    )
    return order


@contextmanager
def _patch_ingest_deps(qdrant, bm25=None, embed=_embed_ok, cache=None):
    """统一 patch 任务依赖：get_qdrant / get_bm25 / embed_sync / cache_invalidate。"""
    embed_mock = MagicMock(return_value=None) if embed is None else MagicMock(side_effect=embed)
    with (
        patch.object(tasks, "get_qdrant", return_value=qdrant),
        patch.object(tasks, "get_bm25", return_value=bm25 if bm25 is not None else MagicMock()),
        patch.object(tasks, "embed_sync", embed_mock),
        patch.object(tasks, "cache_invalidate", cache if cache is not None else AsyncMock()),
    ):
        yield


# ---------- 1. 成功链路（ingest_uploaded_document） ----------


async def test_ingest_md_success(tmp_path):
    """md 成功：ingested=True、doc_id=uploads/<stem>、先删后写 + 全量 BM25 重建。"""
    f = tmp_path / "退货规则.md"
    f.write_text("# 退货规则\n\n7 天无理由退货。", encoding="utf-8")

    qdrant = _make_qdrant(scroll_chunks=[{"doc_id": "uploads/退货规则", "text": "c1"}])
    bm25 = MagicMock()
    cache = AsyncMock()
    with _patch_ingest_deps(qdrant, bm25=bm25, cache=cache):
        order = _track_write_order(qdrant)
        result = await tasks.ingest_uploaded_document(str(f), "退货规则.md", "业务规则")

    assert result == {"ingested": True, "doc_id": "uploads/退货规则", "chunks": 1}
    # worker 冷启动按需 connect（幂等）
    qdrant.connect.assert_awaited()
    # 先 delete_by_doc 后 upsert（旧 chunks 先删后建，不新旧混合）
    assert order == ["delete", "upsert"]
    # 写入成功后按 scroll_all 全量重建 BM25 + 版本广播 + 语义缓存失效
    qdrant.scroll_all.assert_awaited_once()
    bm25.build.assert_called_once_with([{"doc_id": "uploads/退货规则", "text": "c1"}])
    bm25.mark_changed.assert_called_once()
    cache.assert_awaited_once()
    # chunk 元数据形状与 seed 脚本一致（source/category/security_group）
    chunks, embeddings = qdrant.upsert.call_args[0]
    assert chunks[0]["doc_id"] == "uploads/退货规则"
    assert chunks[0]["title"] == "退货规则.md"
    assert chunks[0]["source"] == "knowledge/uploads/退货规则.md"
    assert chunks[0]["category"] == "业务规则"
    assert chunks[0]["security_group"] == ["user", "agent", "admin"]
    assert len(embeddings) == len(chunks)


async def test_ingest_default_category_is_upload(tmp_path):
    """category 未传：metadata category 回落 "upload"；上传链路不写 enabled。"""
    f = tmp_path / "a.md"
    f.write_text("内容", encoding="utf-8")
    qdrant = _make_qdrant()
    with _patch_ingest_deps(qdrant):
        await tasks.ingest_uploaded_document(str(f), "a.md")
    chunks = qdrant.upsert.call_args[0][0]
    assert chunks[0]["category"] == "upload"
    assert "enabled" not in chunks[0]


# ---------- 2/3. 失败链路（error dict + warning，不抛栈） ----------


async def test_ingest_file_missing_returns_error(caplog):
    """文件不存在：error dict + warning，不触发 embedding。"""
    embed = MagicMock()
    with _patch_ingest_deps(_make_qdrant(), embed=embed):
        result = await tasks.ingest_uploaded_document(
            os.path.join("no", "such", "file.md"), "file.md"
        )
    assert result["ingested"] is False
    assert "文件不存在" in result["error"]
    assert any("文件不存在" in r.message for r in caplog.records)
    embed.assert_not_called()


async def test_ingest_empty_text_returns_error(caplog):
    """解析后内容为空（空 md）：error dict + warning，不入库。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "empty.md")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("   \n")
        embed = MagicMock()
        qdrant = _make_qdrant()
        with _patch_ingest_deps(qdrant, embed=embed):
            result = await tasks.ingest_uploaded_document(f, "empty.md")
    assert result["ingested"] is False
    assert "文档解析失败或内容为空" in result["error"]
    assert any("内容为空" in r.message for r in caplog.records)
    embed.assert_not_called()
    qdrant.delete_by_doc.assert_not_awaited()


async def test_ingest_parse_failure_returns_error():
    """解析抛异常（损坏 pdf）：error dict + warning，不入库。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "broken.pdf")
        with open(f, "wb") as fh:
            fh.write(b"not a pdf")
        embed = MagicMock()
        with (
            patch.object(tasks, "get_qdrant", return_value=_make_qdrant()),
            patch.object(tasks, "get_bm25", MagicMock()),
            patch.object(tasks, "embed_sync", embed),
            patch.object(tasks, "cache_invalidate", AsyncMock()),
            patch.object(tasks.parsers, "extract_text", side_effect=ValueError("bad pdf")),
        ):
            result = await tasks.ingest_uploaded_document(f, "broken.pdf")
    assert result["ingested"] is False
    assert "文档解析失败或内容为空" in result["error"]
    embed.assert_not_called()


# ---------- 4. Qdrant 不可用 ----------


async def test_ingest_qdrant_unavailable_returns_error(caplog):
    """Qdrant 连接失败：error dict，不删旧点、不触发 BM25 重建。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "a.md")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("内容")
        qdrant = _make_qdrant(connect_ok=False)
        bm25 = MagicMock()
        with _patch_ingest_deps(qdrant, bm25=bm25):
            result = await tasks.ingest_uploaded_document(f, "a.md")
    assert result == {"ingested": False, "error": "Qdrant 不可用，文档入库失败"}
    assert any("Qdrant" in r.message for r in caplog.records)
    qdrant.delete_by_doc.assert_not_awaited()
    qdrant.upsert.assert_not_awaited()
    bm25.build.assert_not_called()
    bm25.mark_changed.assert_not_called()


# ---------- 5. embedding 失败（重灌核心安全断言：旧版本保留） ----------


async def test_ingest_embedding_failure_preserves_old_version(caplog):
    """embed_sync 返回 None：error dict，delete_by_doc 未调用（旧版本保留）。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "a.md")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("新内容")
        qdrant = _make_qdrant(connected=True)
        bm25 = MagicMock()
        with _patch_ingest_deps(qdrant, bm25=bm25, embed=None):
            result = await tasks.ingest_uploaded_document(f, "a.md")
    assert result["ingested"] is False
    assert "embedding" in result["error"]
    assert any("embedding" in r.message.lower() for r in caplog.records)
    # 核心契约：embedding 失败绝不能已删旧点
    qdrant.delete_by_doc.assert_not_awaited()
    qdrant.upsert.assert_not_awaited()
    bm25.build.assert_not_called()


# ---------- 6. reingest_document ----------


async def test_reingest_success(tmp_path):
    """reingest 成功：resolve 命中、delete 先于 upsert、chunks 数正确。"""
    kb = tmp_path / "knowledge"
    updir = kb / "uploads"
    updir.mkdir(parents=True)
    src = updir / "退货规则.md"
    src.write_text(
        "# 退货规则\n\n7 天无理由退货。\n\n## 换货规则\n\n15 日内可换货。", encoding="utf-8"
    )

    qdrant = _make_qdrant(scroll_chunks=[{"doc_id": "uploads/退货规则", "text": "c"}])
    bm25 = MagicMock()
    with (
        _patch_ingest_deps(qdrant, bm25=bm25),
        patch.object(tasks, "KB_ROOT", str(kb)),
    ):
        order = _track_write_order(qdrant)
        result = await tasks.reingest_document("uploads/退货规则")

    assert result == {"ingested": True, "doc_id": "uploads/退货规则", "chunks": 2}
    assert order == ["delete", "upsert"]
    qdrant.scroll_all.assert_awaited_once()
    bm25.build.assert_called_once()
    bm25.mark_changed.assert_called_once()


async def test_reingest_reuses_metadata_from_existing_chunks(tmp_path):
    """元数据复用：scroll_by_doc 首个 chunk 的 category/enabled 等透传到新 chunk。"""
    kb = tmp_path / "knowledge"
    updir = kb / "uploads"
    updir.mkdir(parents=True)
    src = updir / "促销规则.md"
    src.write_text("# 促销规则\n\n满 199 减 30。", encoding="utf-8")

    qdrant = _make_qdrant(
        existing=[
            {
                "chunk_index": 0,
                "doc_id": "uploads/促销规则",
                "title": "促销规则.md",
                "source": "knowledge/uploads/促销规则.md",
                "category": "业务规则",
                "security_group": ["user", "agent"],
                "enabled": False,
                "text": "old",
            }
        ]
    )
    with (
        _patch_ingest_deps(qdrant),
        patch.object(tasks, "KB_ROOT", str(kb)),
    ):
        result = await tasks.reingest_document("uploads/促销规则")

    assert result["ingested"] is True
    chunks = qdrant.upsert.call_args[0][0]
    assert chunks[0]["category"] == "业务规则"
    assert chunks[0]["enabled"] is False
    assert chunks[0]["security_group"] == ["user", "agent"]
    assert chunks[0]["source"] == "knowledge/uploads/促销规则.md"
    assert chunks[0]["title"] == "促销规则.md"


async def test_reingest_source_missing_returns_error(tmp_path, caplog):
    """源文件无法定位：error dict + warning，不触碰 Qdrant。"""
    kb = tmp_path / "knowledge"
    kb.mkdir()
    qdrant = _make_qdrant()
    with (
        _patch_ingest_deps(qdrant),
        patch.object(tasks, "KB_ROOT", str(kb)),
    ):
        result = await tasks.reingest_document("uploads/ghost")
    assert result["ingested"] is False
    assert "未找到源文件" in result["error"]
    assert any("未找到源文件" in r.message for r in caplog.records)
    qdrant.delete_by_doc.assert_not_awaited()
    qdrant.upsert.assert_not_awaited()


async def test_reingest_qdrant_unavailable_returns_error(caplog):
    """Qdrant 不可用：error dict（与既有任务契约一致），不读写。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        kb = os.path.join(d, "knowledge", "uploads")
        os.makedirs(kb)
        src = os.path.join(kb, "a.md")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write("内容")
        qdrant = _make_qdrant(connect_ok=False)
        with (
            _patch_ingest_deps(qdrant),
            patch.object(tasks, "KB_ROOT", os.path.join(d, "knowledge")),
        ):
            result = await tasks.reingest_document("uploads/a")
    assert result["ingested"] is False
    assert "Qdrant" in result["error"]
    qdrant.delete_by_doc.assert_not_awaited()
    qdrant.upsert.assert_not_awaited()


async def test_reingest_embedding_failure_preserves_old_version(tmp_path):
    """reingest 链路同样先 embed 后删：embedding 失败旧版本保留。"""
    kb = tmp_path / "knowledge"
    updir = kb / "uploads"
    updir.mkdir(parents=True)
    src = updir / "a.md"
    src.write_text("新内容", encoding="utf-8")

    qdrant = _make_qdrant(connected=True)
    with (
        _patch_ingest_deps(qdrant, embed=None),
        patch.object(tasks, "KB_ROOT", str(kb)),
    ):
        result = await tasks.reingest_document("uploads/a")
    assert result["ingested"] is False
    assert "embedding" in result["error"]
    qdrant.delete_by_doc.assert_not_awaited()
    qdrant.upsert.assert_not_awaited()


# ---------- 7. resolve_source_path ----------


def test_resolve_source_path_uploads_prefix_hit(tmp_path):
    """uploads/ 前缀：在 KB_ROOT/uploads 下按 stem 匹配允许扩展名。"""
    kb = tmp_path / "knowledge"
    updir = kb / "uploads"
    updir.mkdir(parents=True)
    f = updir / "退货规则.md"
    f.write_text("x", encoding="utf-8")
    with patch.object(tasks, "KB_ROOT", str(kb)):
        assert tasks.resolve_source_path("uploads/退货规则") == str(f)


def test_resolve_source_path_mall_relative_path_hit(tmp_path):
    """mall 子目录 doc_id（business/xxx）：按相对路径递归匹配。"""
    kb = tmp_path / "knowledge"
    subdir = kb / "mall" / "business"
    subdir.mkdir(parents=True)
    f = subdir / "refund.md"
    f.write_text("x", encoding="utf-8")
    with patch.object(tasks, "KB_ROOT", str(kb)):
        assert tasks.resolve_source_path("business/refund") == str(f)


def test_resolve_source_path_mtime_latest_wins(tmp_path):
    """多扩展名命中（a.md 与 a.txt 并存）：取 mtime 最新。"""
    kb = tmp_path / "knowledge"
    updir = kb / "uploads"
    updir.mkdir(parents=True)
    older = updir / "a.md"
    newer = updir / "a.txt"
    older.write_text("md", encoding="utf-8")
    newer.write_text("txt", encoding="utf-8")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    with patch.object(tasks, "KB_ROOT", str(kb)):
        assert tasks.resolve_source_path("uploads/a") == str(newer)


def test_resolve_source_path_no_hit_returns_none(tmp_path):
    """无命中返回 None（uploads 与 mall/ops 均不命中）。"""
    kb = tmp_path / "knowledge"
    (kb / "mall").mkdir(parents=True)
    with patch.object(tasks, "KB_ROOT", str(kb)):
        assert tasks.resolve_source_path("uploads/ghost") is None
        assert tasks.resolve_source_path("mall/ghost") is None


# ---------- 8. RQ 可序列化契约 ----------


def test_task_functions_are_module_level():
    """任务函数必须模块级（RQ 按模块路径引用）且参数可 pickle。"""
    for fn in (tasks.ingest_uploaded_document, tasks.reingest_document):
        assert inspect.isfunction(fn)
        assert fn.__qualname__ == fn.__name__
        assert fn.__module__ == "app.core.tasks"
    payload = {"file_path": "a.md", "filename": "a.md", "category": ""}
    assert pickle.loads(pickle.dumps(payload)) == payload


# ---------- 9. 端到端（fakeredis + burst worker） ----------


def test_ingest_enqueue_and_burst_worker_executes(tmp_path):
    """端到端：入队 → burst worker 执行 async 任务 → finished + ingested 结果。

    Windows 无 os.fork，用 SimpleWorker（同进程执行）。
    """
    f = tmp_path / "hello.md"
    f.write_text("# hello\n\n世界", encoding="utf-8")

    fake = FakeRedis()
    queue = Queue("assistmind", connection=fake)
    qdrant = _make_qdrant(scroll_chunks=[{"doc_id": "uploads/hello", "text": "c"}])
    bm25 = MagicMock()
    with (
        _patch_ingest_deps(qdrant, bm25=bm25),
        patch.object(tasks, "get_queue", lambda: queue),
    ):
        job = tasks.enqueue_task(
            tasks.ingest_uploaded_document,
            file_path=str(f),
            filename="hello.md",
            category="",
        )
        worker = SimpleWorker([queue], connection=fake)
        worker.work(burst=True)

        job.refresh()
        assert job.get_status() == "finished"
        assert job.return_value() == {"ingested": True, "doc_id": "uploads/hello", "chunks": 1}
    bm25.build.assert_called_once()
    bm25.mark_changed.assert_called_once()
