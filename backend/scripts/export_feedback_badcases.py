"""把低分反馈（score<=2）回流为评估集 bad case（RAG 回归 / A-B 对照用）。

用法（backend/ 下）：
    venv\\Scripts\\python.exe scripts/export_feedback_badcases.py [--limit 50]

流程（Bad Case 五步法中的「回归」一环，对应 run_eval.py 的无 ground_truth 模式）：
1. 读 feedbacks 表 score<=2 且 exported=false 的反馈（score 1-2 为差评样本）
2. 生成 backend/app/data/eval_feedback.json：
   - question 取反馈 query；ground_truth 留空（线上样例无标准答案，
     run_eval.py 检测到无 ground_truth 样本时自动跳过 context_recall）
   - ground_truth 留空时建议人工补填后再做正式回归（可选）
   - 与已有 eval_feedback.json 按 question 去重，避免重复回流
3. 将已导出反馈标记 exported=true（幂等，重复运行不产生重复样本）
4. 打印回归命令提示

依赖 PostgreSQL（feedback_service 走 async_session）。脚本无 I/O 时安全退出。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.feedback_service import list_feedback, mark_exported
from app.core.infra.postgres import engine

logger = logging.getLogger(__name__)

# backend/scripts/export_feedback_badcases.py → backend/app/data/eval_feedback.json
OUT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "app", "data", "eval_feedback.json"
)
BAD_SCORE_MAX = 2  # 差评阈值：score<=2 视为 bad case


def _load_existing_questions() -> set[str]:
    """读取已有 eval_feedback.json 的 question 集合（去重用）。"""
    if not os.path.exists(OUT_PATH):
        return set()
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {str(x.get("question", "")).strip() for x in data if isinstance(x, dict)}
    except (OSError, ValueError):
        logger.warning("[Export] 读取现有 %s 失败，按空集处理", OUT_PATH)
        return set()


def _save(entries: list[dict]) -> None:
    """合并写入 eval_feedback.json（保持已有条目）。"""
    existing = []
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, ValueError):
            logger.warning("[Export] 现有文件不可读，将覆盖重建: %s", OUT_PATH)
            existing = []
    merged = existing + entries
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    logger.info("[Export] 已写入 %s（共 %d 条）", OUT_PATH, len(merged))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="低分反馈回流评估集")
    parser.add_argument("--limit", type=int, default=50, help="本次最多导出条数")
    args = parser.parse_args()

    existing_qs = _load_existing_questions()

    page = 1
    page_size = min(args.limit * 5 or 100, 200)  # 一次拉取足够批次避免多次翻页
    collected: list[dict] = []
    while page_size and len(collected) < args.limit:
        res = await list_feedback(
            score=None, exported=False, page=page, page_size=page_size
        )
        items = [
            it
            for it in res.get("items", [])
            if it.get("score", 5) <= BAD_SCORE_MAX
            and (it.get("query") or "").strip()
        ]
        for it in items:
            q = it["query"].strip()
            if q in existing_qs:
                continue  # 已回流过，跳过
            collected.append(it)
            existing_qs.add(q)
            if len(collected) >= args.limit:
                break
        total = res.get("total", 0)
        if page * page_size >= total:
            break
        page += 1

    if not collected:
        logger.info("[Export] 没有新的低分反馈待回流（score<=%d 且 query 非空）", BAD_SCORE_MAX)
        await engine.dispose()
        return

    entries = []
    for it in collected:
        entries.append(
            {
                "question": it["query"].strip(),
                "ground_truth": "",  # 无标准答案；run_eval 会自动跳过 context_recall
                "adversarial": True,  # 差评样本视为对抗回归样本（不许回退）
                "source": f"feedback:{it.get('id', '')}",
                "trace_id": it.get("trace_id") or "",
            }
        )

    _save(entries)
    exported = await mark_exported([it["id"] for it in collected])
    logger.info(
        "[Export] 回流完成：导出 %d 条 bad case，标记 %d 条已回流",
        len(entries),
        exported,
    )

    print("\n===== Bad Case 回归（A/B 对照）=====")
    print(f"已回流 {len(entries)} 条低分反馈到 {OUT_PATH}")
    print("回归命令：")
    print("    venv\\Scripts\\python.exe scripts/run_eval.py app/data/eval_feedback.json")
    print(
        "判读：answer_relevancy / context_precision 提升 = 检索与回答改善；"
        "runner 改造前后各跑一次 diff 即归因证据。"
    )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
