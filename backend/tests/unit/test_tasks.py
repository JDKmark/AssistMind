"""RQ 异步任务模块单元测试（P0：消息队列）。

覆盖：
- 任务函数行为：rebuild_knowledge_base（正常/Qdrant 不可用）、
  export_badcases（回流/去重/标记）、run_evaluation（subprocess 包裹）
- 队列接入：enqueue_task 走 get_queue、fetch_job 未知 job 返回 None
- 端到端（fakeredis + burst worker）：入队 → worker 执行 → finished + result

mock 策略：任务依赖（Qdrant/BM25/feedback_service/subprocess）全 mock，
队列用 fakeredis（dev 依赖已有），不连真实 Redis。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fakeredis import FakeRedis
from rq import Queue, SimpleWorker

import app.core.tasks as tasks

# ---------- rebuild_knowledge_base ----------


async def test_rebuild_task_builds_bm25():
    """重建任务：scroll_all 全量 → BM25 build，返回 rebuilt/chunks。"""
    chunks = [{"doc_id": "d1", "text": "t"}]
    qdrant = MagicMock()
    qdrant.is_connected = True
    qdrant.scroll_all = AsyncMock(return_value=chunks)
    bm25 = MagicMock()
    with (
        patch.object(tasks, "get_qdrant", return_value=qdrant),
        patch.object(tasks, "get_bm25", return_value=bm25),
    ):
        result = await tasks.rebuild_knowledge_base()
    assert result == {"rebuilt": True, "chunks": 1}
    qdrant.scroll_all.assert_awaited_once()
    bm25.build.assert_called_once_with(chunks)


async def test_rebuild_task_qdrant_down_returns_error(caplog):
    """Qdrant 不可用：连接失败仍返回 error dict + warning（不静默、不抛栈）。

    worker 无 lifespan，任务内会先按需 connect()——mock connect 失败后
    is_connected 保持 False，走降级分支。
    """
    qdrant = MagicMock()
    qdrant.is_connected = False
    qdrant.connect = AsyncMock()
    with patch.object(tasks, "get_qdrant", return_value=qdrant):
        result = await tasks.rebuild_knowledge_base()
    assert result["rebuilt"] is False
    assert "Qdrant" in result["error"]
    assert any("Qdrant" in r.message for r in caplog.records)
    qdrant.connect.assert_awaited_once()


async def test_rebuild_task_connects_when_worker_cold_start():
    """worker 冷启动（无 lifespan 连接）：任务内按需 connect 后正常重建。"""
    chunks = [{"doc_id": "d1", "text": "t"}]
    qdrant = MagicMock()
    qdrant.is_connected = False
    qdrant.connect = AsyncMock(side_effect=lambda: setattr(qdrant, "is_connected", True))
    qdrant.scroll_all = AsyncMock(return_value=chunks)
    bm25 = MagicMock()
    with (
        patch.object(tasks, "get_qdrant", return_value=qdrant),
        patch.object(tasks, "get_bm25", return_value=bm25),
    ):
        result = await tasks.rebuild_knowledge_base()
    assert result == {"rebuilt": True, "chunks": 1}
    qdrant.connect.assert_awaited_once()
    qdrant.scroll_all.assert_awaited_once()


# ---------- export_badcases ----------


async def test_export_badcases_collects_marks_and_saves(tmp_path, monkeypatch):
    """回流任务：收集低分反馈 → 写 eval_feedback.json → 标记 exported。"""
    out = tmp_path / "eval_feedback.json"
    monkeypatch.setattr(tasks, "OUT_PATH", str(out))

    items = [
        {"id": 1, "score": 1, "query": "答非所问", "trace_id": "t1"},
        {"id": 2, "score": 2, "query": "检索不准", "trace_id": "t2"},
    ]
    list_feedback = AsyncMock(return_value={"items": items, "total": 2})
    mark_exported = AsyncMock(return_value=2)
    with (
        patch.object(tasks, "list_feedback", list_feedback),
        patch.object(tasks, "mark_exported", mark_exported),
    ):
        result = await tasks.export_badcases()

    assert result == {"exported": 2, "marked": 2}
    assert out.exists()
    import json

    entries = json.loads(out.read_text(encoding="utf-8"))
    assert [e["question"] for e in entries] == ["答非所问", "检索不准"]
    assert all(e["adversarial"] is True for e in entries)
    mark_exported.assert_awaited_once_with([1, 2])


async def test_export_badcases_no_new_returns_zero(tmp_path, monkeypatch):
    """无待回流样本：返回 exported=0，不写文件、不标记。"""
    out = tmp_path / "eval_feedback.json"
    monkeypatch.setattr(tasks, "OUT_PATH", str(out))
    with (
        patch.object(tasks, "list_feedback", AsyncMock(return_value={"items": [], "total": 0})),
        patch.object(tasks, "mark_exported", AsyncMock()),
    ):
        result = await tasks.export_badcases()
    assert result == {"exported": 0, "marked": 0}
    assert not out.exists()


async def test_export_badcases_dedup_by_question(tmp_path, monkeypatch):
    """去重：已回流的 question 不重复写入（幂等）。"""
    out = tmp_path / "eval_feedback.json"
    monkeypatch.setattr(tasks, "OUT_PATH", str(out))
    out.write_text(
        '[{"question": "答非所问", "ground_truth": "", "adversarial": true}]',
        encoding="utf-8",
    )
    items = [
        {"id": 3, "score": 1, "query": "答非所问", "trace_id": ""},
        {"id": 4, "score": 2, "query": "新问题", "trace_id": ""},
    ]
    with (
        patch.object(tasks, "list_feedback", AsyncMock(return_value={"items": items, "total": 2})),
        patch.object(tasks, "mark_exported", AsyncMock(return_value=1)),
    ):
        result = await tasks.export_badcases()
    assert result == {"exported": 1, "marked": 1}


# ---------- run_evaluation ----------


def test_run_evaluation_subprocess_ok():
    """评估任务：subprocess 跑 run_eval.py，成功返回 ok + stdout。"""
    proc = MagicMock(returncode=0, stdout="metrics ok", stderr="")
    with patch.object(tasks.subprocess, "run", return_value=proc) as mock_run:
        result = tasks.run_evaluation("app/data/eval_feedback.json")
    assert result["ok"] is True
    assert result["stdout"] == "metrics ok"
    cmd = mock_run.call_args[0][0]
    assert "scripts/run_eval.py" in " ".join(str(c) for c in cmd)
    assert "eval_feedback.json" in " ".join(str(c) for c in cmd)


def test_run_evaluation_subprocess_fail_logged():
    """评估任务：非零退出码返回 ok=False + stderr，不抛异常。"""
    proc = MagicMock(returncode=1, stdout="", stderr="boom")
    with patch.object(tasks.subprocess, "run", return_value=proc):
        result = tasks.run_evaluation()
    assert result["ok"] is False
    assert result["stderr"] == "boom"


# ---------- 队列接入 ----------


def test_enqueue_task_uses_queue_with_retry_and_timeout():
    """enqueue_task：经 get_queue 入队，带 Retry(max=2) 与 timeout。"""
    fake = FakeRedis()
    queue = Queue("assistmind", connection=fake)
    monkeypatch_queue = patch.object(tasks, "get_queue", lambda: queue)
    with monkeypatch_queue:
        job = tasks.enqueue_task(tasks.run_evaluation)
        job.refresh()
    assert job is not None
    assert job.timeout == 1800
    assert job.retries_left == 2  # Retry(max=2) 的落库形态
    assert queue.count == 1


def test_fetch_job_unknown_returns_none():
    """fetch_job：不存在的 job_id 返回 None（不抛 NoSuchJobError）。"""
    fake = FakeRedis()
    queue = Queue("assistmind", connection=fake)
    with patch.object(tasks, "get_queue", lambda: queue):
        assert tasks.fetch_job("no-such-job") is None


# ---------- 端到端（fakeredis + burst worker） ----------


def test_enqueue_and_burst_worker_executes_rebuild():
    """端到端：入队 → burst worker 执行 async 任务 → finished + result 可查。

    Windows 无 os.fork，用 SimpleWorker（同进程执行）；Linux 生产用 fork Worker。
    """
    fake = FakeRedis()
    queue = Queue("assistmind", connection=fake)
    chunks = [{"doc_id": "d1", "text": "t"}]
    qdrant = MagicMock()
    qdrant.is_connected = True
    qdrant.scroll_all = AsyncMock(return_value=chunks)
    bm25 = MagicMock()
    with (
        patch.object(tasks, "get_qdrant", return_value=qdrant),
        patch.object(tasks, "get_bm25", return_value=bm25),
        patch.object(tasks, "get_queue", lambda: queue),
    ):
        job = tasks.enqueue_task(tasks.rebuild_knowledge_base)
        worker = SimpleWorker([queue], connection=fake)
        worker.work(burst=True)

        job.refresh()
        assert job.get_status() == "finished"
        assert job.return_value() == {"rebuilt": True, "chunks": 1}
    bm25.build.assert_called_once_with(chunks)


# ---------- _save_entries 原子写 ----------


def test_save_entries_atomic_replace(tmp_path):
    """_save_entries：tmp + os.replace 原子替换——保留已有条目，无 .tmp 残留。

    回归背景：直接 "w" 覆盖写在 worker 崩溃/重启时会产生截断 JSON，
    下次读取失败静默清空全部历史 bad case。
    """
    import json as _json

    out = tmp_path / "eval_feedback.json"
    out.write_text('[{"question": "旧问题"}]', encoding="utf-8")

    tasks._save_entries(str(out), [{"question": "新问题"}])

    data = _json.loads(out.read_text(encoding="utf-8"))
    assert [x["question"] for x in data] == ["旧问题", "新问题"]
    assert not list(tmp_path.glob("*.tmp"))
