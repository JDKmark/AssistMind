"""灌入运维知识库到 Qdrant。

用法：python scripts/seed_ops_kb.py [--reset]
- 读取 knowledge/ops/ 下文档（.md/.txt/.pdf/.docx），分块 + embedding + 写入 Qdrant
- PDF/DOCX 由 parsers.py 解析并注入 `# 文件名` 标题（保留文档归属语义）
- --reset：先清空集合再写入（幂等重建）
- 进程内同时构建 BM25 索引（供当前进程检索使用）
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os

from app.core.rag.parsers import extract_text
from app.core.rag.seeder import seed_docs

logger = logging.getLogger(__name__)

# knowledge/ops/ 位于仓库根，脚本从 backend/ 运行时需回退两级
_KB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "knowledge", "ops")

_SUPPORTED_EXTS = (".md", ".txt", ".pdf", ".docx")


def _read_doc_text(path: str, title: str) -> str | None:
    """读取文档文本；单个二进制文档解析失败时记 warning 并跳过（不阻塞灌库）。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        text = extract_text(path)
    except Exception as e:
        logger.warning("[SeedOpsKB] 跳过无法解析的文档 %s: %s", path, e)
        return None
    if ext in (".pdf", ".docx"):
        text = f"# {title}\n\n{text}"
    return text


def _load_docs() -> list[dict]:
    """读取知识库文档，返回 [{doc_id, title, text}]。"""
    docs = []
    for path in sorted(glob.glob(os.path.join(_KB_DIR, "*"))):
        if not path.lower().endswith(_SUPPORTED_EXTS):
            continue
        if not os.path.isfile(path):
            continue
        doc_id = os.path.splitext(os.path.basename(path))[0]
        title = doc_id
        text = _read_doc_text(path, title)
        if text is None:
            continue
        docs.append(
            {
                "doc_id": doc_id,
                "title": title,
                "text": text,
                "ext": os.path.splitext(path)[1].lower(),
            }
        )
    return docs


def _chunk_metadata(doc: dict) -> dict:
    """chunk 元数据：Qdrant payload 中 doc_id/title/source/category/security_group 保留，
    结构感知分块新增的 section_title / table_comment 由 chunk 字典自动带上（见 qdrant.upsert）。"""
    return {
        "doc_id": doc["doc_id"],
        "title": doc["title"],
        "source": f"knowledge/ops/{doc['doc_id']}{doc.get('ext', '.md')}",
        "category": "ops",
        "security_group": ["user", "agent", "admin"],
    }


async def seed(reset: bool = False) -> dict:
    """灌库主逻辑（chunk → embedding → upsert → BM25，见 seeder.seed_docs）。"""
    return await seed_docs(_load_docs(), metadata_fn=_chunk_metadata, reset=reset, log_prefix="SeedKB")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import sys

    reset = "--reset" in sys.argv
    stats = await seed(reset=reset)
    print("灌库完成:", stats)


if __name__ == "__main__":
    asyncio.run(main())
