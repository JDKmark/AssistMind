"""RQ worker 启动入口（消费 assistmind 队列的异步任务）。

用法（backend/ 下）：
    venv\\Scripts\\python.exe scripts/run_worker.py          # 前台运行
    python scripts/run_worker.py                            # Linux / 容器内

Linux（含 Docker）默认 fork 模式 Worker（任务崩溃不影响 worker 进程）；
Windows 无 os.fork，自动降级 SimpleWorker（同进程执行）。
任务定义见 app/core/tasks/__init__.py（rebuild_knowledge_base / export_badcases /
run_evaluation），入队方为 app/api/knowledge.py 的 rebuild 端点等。
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rq import SimpleWorker, Worker  # noqa: E402

from app.core.tasks import get_queue  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    queue = get_queue()
    # Windows 无 os.fork → SimpleWorker；Linux/容器用 fork Worker（任务隔离更稳）
    worker_cls = Worker if hasattr(os, "fork") else SimpleWorker
    worker = worker_cls([queue], connection=queue.connection)
    logging.info(
        "[Worker] 启动 %s，监听队列 assistmind（Redis=%s）",
        worker_cls.__name__,
        queue.connection.connection_pool.connection_kwargs.get("host", "redis"),
    )
    worker.work()


if __name__ == "__main__":
    main()
