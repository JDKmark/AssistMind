"""把低分反馈（score<=2）回流为评估集 bad case（RAG 回归 / A-B 对照用）。

用法（backend/ 下）：
    venv\\Scripts\\python.exe scripts/export_feedback_badcases.py [--limit 50]

核心逻辑已收编为异步任务 app.core.tasks.export_badcases（可经 RQ 队列异步执行），
本脚本保留为命令行壳：参数解析 + 结果提示 + engine 清理。

流程（Bad Case 五步法中的「回归」一环，对应 run_eval.py 的无 ground_truth 模式）：
1. 读 feedbacks 表 score<=2 且 exported=false 的反馈（score 1-2 为差评样本）
2. 生成 backend/app/data/eval_feedback.json（question 取反馈 query，ground_truth 留空）
3. 将已导出反馈标记 exported=true（幂等，按 question 去重）
4. 打印回归命令提示

依赖 PostgreSQL（feedback_service 走 async_session）。脚本无 I/O 时安全退出。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.infra.postgres import engine  # noqa: E402
from app.core.tasks import OUT_PATH, export_badcases  # noqa: E402


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="低分反馈回流评估集")
    parser.add_argument("--limit", type=int, default=50, help="本次最多导出条数")
    args = parser.parse_args()

    try:
        result = await export_badcases(limit=args.limit)
    finally:
        await engine.dispose()

    if result["exported"] == 0:
        print("没有新的低分反馈待回流（score<=2 且 query 非空）")
        return

    print("\n===== Bad Case 回归（A/B 对照）=====")
    print(f"已回流 {result['exported']} 条低分反馈到 {os.path.abspath(OUT_PATH)}")
    print("回归命令：")
    print("    venv\\Scripts\\python.exe scripts/run_eval.py app/data/eval_feedback.json")
    print(
        "判读：answer_relevancy / context_precision 提升 = 检索与回答改善；"
        "runner 改造前后各跑一次 diff 即归因证据。"
    )


if __name__ == "__main__":
    asyncio.run(main())
