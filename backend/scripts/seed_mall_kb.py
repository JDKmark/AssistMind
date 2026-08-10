"""灌入 mall 商城知识库到 Qdrant。

用法：python scripts/seed_mall_kb.py [--reset]
- 读取 knowledge/mall/ 下全部文档（.md/.sql/.yml，按来源子目录），
  用结构感知 chunk_text 分块（SQL DDL 自动按 CREATE TABLE 切块）+ embedding + 写入 Qdrant
- payload：doc_id/title/source/category="mall"/security_group，
  section_title / table_comment 由结构感知分块自动流入（见 qdrant.upsert）
- --reset：先按 doc_id 清空再写入（幂等重建）
- 进程内同时构建 BM25 索引（供当前进程检索使用）
- Qdrant 不可用时仍输出切分统计，并明确报错退出（exit 1）
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import sys

from app.core.rag.chunking import chunk_text
from app.core.rag.seeder import seed_docs

logger = logging.getLogger(__name__)

# knowledge/mall/ 位于仓库根，脚本从 backend/ 运行时需回退两级
_KB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "knowledge", "mall")

_SUPPORTED_EXTS = (".md", ".sql", ".yml")


def _load_docs() -> list[dict]:
    """读取 knowledge/mall/ 全部文档（递归子目录），返回 [{doc_id, title, rel_path, text}]。

    doc_id 取相对路径（去掉扩展名），保证子目录间唯一且可读，
    如 sql/mall_tables、reference/deploy-windows。
    """
    docs = []
    for path in sorted(glob.glob(os.path.join(_KB_DIR, "**", "*.*"), recursive=True)):
        if not path.lower().endswith(_SUPPORTED_EXTS):
            continue
        # SOURCES.md 是取材说明，不是知识内容，排除（避免污染检索）
        if os.path.basename(path).lower() == "sources.md":
            continue
        rel = os.path.relpath(path, _KB_DIR).replace("\\", "/")
        doc_id = os.path.splitext(rel)[0]
        title = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            text = f.read()
        docs.append(
            {"doc_id": doc_id, "title": title, "rel_path": rel, "text": text}
        )
    return docs


def _chunk_metadata(doc: dict) -> dict:
    """chunk 元数据：Qdrant payload 中 doc_id/title/source/category/security_group 保留，
    结构感知分块新增的 section_title / table_comment 由 chunk 字典自动带上（见 qdrant.upsert）。"""
    return {
        "doc_id": doc["doc_id"],
        "title": doc["title"],
        "source": f"knowledge/mall/{doc['rel_path']}",
        "category": "mall",
        "security_group": ["user", "agent", "admin"],
    }


async def seed(reset: bool = False) -> dict:
    """灌库主逻辑（chunk → embedding → upsert → BM25，见 seeder.seed_docs）。"""
    return await seed_docs(_load_docs(), metadata_fn=_chunk_metadata, reset=reset, log_prefix="SeedMallKB")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    reset = "--reset" in sys.argv

    # 无论 Qdrant 是否可用，先输出切分统计（脚本必须能走到切分）
    docs = _load_docs()
    per_doc: list[tuple[str, int, int]] = []
    for doc in docs:
        chunks = chunk_text(doc["text"], metadata=_chunk_metadata(doc))
        table_chunks = sum(1 for c in chunks if c.get("table_comment"))
        per_doc.append((doc["doc_id"], len(chunks), table_chunks))
    chunk_total = sum(n for _, n, _ in per_doc)
    table_total = sum(t for _, _, t in per_doc)

    stats = await seed(reset=reset)
    if not stats.get("qdrant_ok"):
        print(
            "[SeedMallKB] 错误：Qdrant 不可用（http://localhost:6333），"
            "请先启动 docker compose 的 qdrant 服务再灌库。"
        )
        print(
            "[SeedMallKB] 切分统计（未写入）：%d 篇文档 / %d 个 chunk（含表结构 chunk %d 个）"
            % (len(docs), chunk_total, table_total)
        )
        for doc_id, n, t in per_doc:
            print(f"  {doc_id}: {n} chunks (table={t})")
        raise SystemExit(1)

    print(
        "灌库完成: 文档数=%d, chunk 数=%d（含表结构 chunk %d 个），"
        "向量写入=%d, BM25 索引=%d"
        % (
            stats["docs"],
            stats["chunks"],
            table_total,
            stats["vector_written"],
            stats["bm25_built"],
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
