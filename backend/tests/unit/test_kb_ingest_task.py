"""上传文档入库任务（ingest_uploaded_document）单元测试。

覆盖：
1. md 成功入库：ingested=True、doc_id=uploads/<stem>、全量重建 BM25 + mark_changed
2. 文件不存在：error dict + warning（不抛栈）
3. 解析空文本：error dict + warning
4. Qdrant 不可用（qdrant_ok=False）：error dict、不触发 BM25
5. 函数模块级可被 RQ 引用（inspect.isfunction + 参数可 pickle）
6. 端到端：fakeredis 队列 + SimpleWorker(burst=True) 入队-消费（mock 点同上）

mock 策略：seed_docs / qdrant / bm25 全 mock，不连外部服务（规范同 test_tasks.py）。
"""

from __future__ import annotations

import inspect
import os
import pickle
from unittest.mock import AsyncMock, MagicMock, patch

from fakeredis import FakeRedis
from rq import Queue, SimpleWorker

import app.core.tasks as tasks


def _make_qdrant(chunks=None, connected=False):
    """构造 mock qdrant：connect 后置 connected=True（模拟按需重连成功）。"""
    qdrant = MagicMock()
    qdrant.is_connected = connected
    qdrant.connect = AsyncMock(side_effect=lambda: setattr(qdrant, "is_connected", True))
    qdrant.scroll_all = AsyncMock(return_value=chunks if chunks is not None else [])
    return qdrant


def _seed_result(vector_written=2, qdrant_ok=True):
    return {
        "docs": 1,
        "chunks": vector_written,
        "vector_written": vector_written,
        "bm25_built": vector_written,
        "qdrant_ok": qdrant_ok,
    }


# ---------- 1. 成功链路 ----------


async def test_ingest_md_success(tmp_path):
    """md 成功：ingested=True、doc_id=uploads/<stem>、seed_docs(reset=True) + 全量 BM25 重建。"""
    f = tmp_path / "退货规则.md"
    f.write_text("# 退货规则\n\n7 天无理由退货。", encoding="utf-8")

    qdrant = _make_qdrant(chunks=[{"doc_id": "uploads/退货规则", "text": "c1"}])
    bm25 = MagicMock()
    seed_docs = AsyncMock(return_value=_seed_result(vector_written=2))
    with (
        patch.object(tasks, "get_qdrant", return_value=qdrant),
        patch.object(tasks, "get_bm25", return_value=bm25),
        patch.object(tasks, "seed_docs", seed_docs),
    ):
        result = await tasks.ingest_uploaded_document(str(f), "退货规则.md", "业务规则")

    assert result == {"ingested": True, "doc_id": "uploads/退货规则", "chunks": 2}
    # worker 冷启动按需 connect（seed_docs 结束会 close，任务内重连后全量重建）
    qdrant.connect.assert_awaited()
    qdrant.scroll_all.assert_awaited_once()
    bm25.build.assert_called_once()
    bm25.mark_changed.assert_called_once()
    # seed_docs 调用契约：单文档 + reset + IngestUpload 前缀
    docs, kwargs = seed_docs.call_args[0][0], seed_docs.call_args[1]
    assert len(docs) == 1
    assert docs[0]["doc_id"] == "uploads/退货规则"
    assert docs[0]["title"] == "退货规则.md"
    assert kwargs["reset"] is True
    assert kwargs["log_prefix"] == "IngestUpload"
    # metadata 形状与 seed 脚本一致（source/category/security_group）
    meta = seed_docs.call_args[0][1](docs[0])
    assert meta == {
        "doc_id": "uploads/退货规则",
        "title": "退货规则.md",
        "source": "knowledge/uploads/退货规则.md",
        "category": "业务规则",
        "security_group": ["user", "agent", "admin"],
    }


async def test_ingest_default_category_is_upload(tmp_path):
    """category 未传：metadata category 回落 "upload"。"""
    f = tmp_path / "a.md"
    f.write_text("内容", encoding="utf-8")
    seed_docs = AsyncMock(return_value=_seed_result())
    with (
        patch.object(tasks, "get_qdrant", return_value=_make_qdrant()),
        patch.object(tasks, "get_bm25", MagicMock()),
        patch.object(tasks, "seed_docs", seed_docs),
    ):
        await tasks.ingest_uploaded_document(str(f), "a.md")
    meta = seed_docs.call_args[0][1]({"doc_id": "uploads/a", "title": "a.md"})
    assert meta["category"] == "upload"


# ---------- 2/3. 失败链路（error dict + warning，不抛栈） ----------


async def test_ingest_file_missing_returns_error(caplog):
    """文件不存在：error dict + warning，不触发 seed_docs。"""
    seed_docs = AsyncMock()
    with patch.object(tasks, "seed_docs", seed_docs):
        result = await tasks.ingest_uploaded_document(
            os.path.join("no", "such", "file.md"), "file.md"
        )
    assert result["ingested"] is False
    assert "文件不存在" in result["error"]
    assert any("文件不存在" in r.message for r in caplog.records)
    seed_docs.assert_not_awaited()


async def test_ingest_empty_text_returns_error(caplog):
    """解析后内容为空（空 md）：error dict + warning，不入库。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "empty.md")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("   \n")
        seed_docs = AsyncMock()
        with patch.object(tasks, "seed_docs", seed_docs):
            result = await tasks.ingest_uploaded_document(f, "empty.md")
    assert result["ingested"] is False
    assert "文档解析失败或内容为空" in result["error"]
    assert any("内容为空" in r.message for r in caplog.records)
    seed_docs.assert_not_awaited()


async def test_ingest_parse_failure_returns_error():
    """解析抛异常（损坏 pdf）：error dict + warning，不入库。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "broken.pdf")
        with open(f, "wb") as fh:
            fh.write(b"not a pdf")
        with patch.object(
            tasks.parsers, "extract_text", side_effect=ValueError("bad pdf")
        ):
            result = await tasks.ingest_uploaded_document(f, "broken.pdf")
    assert result["ingested"] is False
    assert "文档解析失败或内容为空" in result["error"]


# ---------- 4. Qdrant 不可用 ----------


async def test_ingest_qdrant_unavailable_returns_error(caplog):
    """seed_docs 返回 qdrant_ok=False：error dict，不触发 BM25 重建。"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "a.md")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("内容")
        qdrant = _make_qdrant()
        bm25 = MagicMock()
        with (
            patch.object(tasks, "get_qdrant", return_value=qdrant),
            patch.object(tasks, "get_bm25", return_value=bm25),
            patch.object(tasks, "seed_docs", AsyncMock(return_value=_seed_result(qdrant_ok=False))),
        ):
            result = await tasks.ingest_uploaded_document(f, "a.md")
    assert result == {"ingested": False, "error": "Qdrant 不可用，文档入库失败"}
    assert any("Qdrant" in r.message for r in caplog.records)
    bm25.build.assert_not_called()
    bm25.mark_changed.assert_not_called()


# ---------- 5. RQ 可序列化契约 ----------


def test_ingest_uploaded_document_is_module_level_function():
    """任务函数必须模块级（RQ 按模块路径引用）且参数可 pickle。"""
    assert inspect.isfunction(tasks.ingest_uploaded_document)
    assert tasks.ingest_uploaded_document.__qualname__ == "ingest_uploaded_document"
    assert tasks.ingest_uploaded_document.__module__ == "app.core.tasks"
    payload = {"file_path": "a.md", "filename": "a.md", "category": ""}
    assert pickle.loads(pickle.dumps(payload)) == payload


# ---------- 6. 端到端（fakeredis + burst worker） ----------


def test_ingest_enqueue_and_burst_worker_executes(tmp_path):
    """端到端：入队 → burst worker 执行 async 任务 → finished + ingested 结果。

    Windows 无 os.fork，用 SimpleWorker（同进程执行）。
    """
    f = tmp_path / "hello.md"
    f.write_text("# hello\n\n世界", encoding="utf-8")

    fake = FakeRedis()
    queue = Queue("assistmind", connection=fake)
    qdrant = _make_qdrant(chunks=[{"doc_id": "uploads/hello", "text": "c"}])
    bm25 = MagicMock()
    with (
        patch.object(tasks, "get_qdrant", return_value=qdrant),
        patch.object(tasks, "get_bm25", return_value=bm25),
        patch.object(tasks, "seed_docs", AsyncMock(return_value=_seed_result(vector_written=3))),
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
        assert job.return_value() == {"ingested": True, "doc_id": "uploads/hello", "chunks": 3}
    bm25.build.assert_called_once()
    bm25.mark_changed.assert_called_once()
