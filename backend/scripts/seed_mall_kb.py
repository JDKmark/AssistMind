"""灌入 mall 商城知识库到 Qdrant。

用法：python scripts/seed_mall_kb.py [--reset] [--file <pdf/docx 路径>]
- 读取 knowledge/mall/ 下全部文档（.md/.sql/.yml/.pdf/.docx，按来源子目录），
  用结构感知 chunk_text 分块（SQL DDL 自动按 CREATE TABLE 切块）+ embedding + 写入 Qdrant
- payload：doc_id/title/source/category="mall"/security_group，
  section_title / table_comment 由结构感知分块自动流入（见 qdrant.upsert）
- --file <路径>：只灌指定单个文件（企业 PDF 常为单文件，便于增量入库），
  PDF/DOCX 由 parsers.py 解析并注入 `# 文件名` 标题（保留文档归属语义）
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
from app.core.rag.parsers import extract_text
from app.core.rag.seeder import seed_docs

logger = logging.getLogger(__name__)

# knowledge/mall/ 位于仓库根，脚本从 backend/ 运行时需回退两级
_KB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "knowledge", "mall")

_SUPPORTED_EXTS = (".md", ".sql", ".yml", ".pdf", ".docx")


def _read_doc_text(path: str, title: str, rel: str) -> str | None:
    """读取文档文本；单个二进制文档解析失败时记 warning 并跳过（不阻塞灌库）。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        text = extract_text(path)
    except Exception as e:
        logger.warning("[SeedMallKB] 跳过无法解析的文档 %s: %s", rel, e)
        return None
    # PDF/DOCX 无 Markdown 结构：把文件名作为首位标题，结构感知切块把标题并入
    # 首个 chunk，LLM 可据此确认文档归属（避免「资料未提及」元话语）
    if ext in (".pdf", ".docx"):
        text = f"# {title}\n\n{text}"
    return text


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
        text = _read_doc_text(path, title, rel)
        if text is None:
            continue
        docs.append(
            {"doc_id": doc_id, "title": title, "rel_path": rel, "text": text}
        )
    return docs


def _apply_file_override(docs: list[dict], arg_path: str) -> list[dict]:
    """--file 单文件入库：只灌指定文件（doc_id 用文件名）。"""
    if not arg_path:
        return docs
    path = os.path.abspath(arg_path)
    if not os.path.exists(path):
        logger.error("[SeedMallKB] --file 指定的文件不存在: %s", path)
        raise SystemExit(1)
    base = os.path.basename(path)
    title = os.path.splitext(base)[0]
    text = _read_doc_text(path, title, base)
    if text is None:
        raise SystemExit(1)
    return [{"doc_id": title, "title": title, "rel_path": base, "text": text}]


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

    args = sys.argv[1:]
    reset = "--reset" in args
    file_path = ""
    if "--file" in args:
        i = args.index("--file")
        if i + 1 < len(args):
            file_path = args[i + 1]

    docs = _load_docs()
    docs = _apply_file_override(docs, file_path)
    total_docs = len(docs)
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
