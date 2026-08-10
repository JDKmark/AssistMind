"""知识库灌库公共逻辑：chunk → embedding → upsert → BM25（seed 脚本共用）。

seed_mall_kb.py / seed_ops_kb.py 的灌库循环完全一致，差异仅在文档加载
（目录/扩展名/排除规则）与 metadata（source/category）——统一到此处，
避免两个知识库的分块行为漂移（如历史 bug：YAML 前缀在 seed 链路未生效）。

返回统计：{docs, chunks, vector_written, bm25_built, qdrant_ok}
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.core.infra.qdrant import get_qdrant
from app.core.rag.bm25 import get_bm25
from app.core.rag.chunking import chunk_text
from app.core.rag.embedding import embed_sync

logger = logging.getLogger(__name__)


async def seed_docs(
    docs: list[dict],
    metadata_fn: Callable[[dict], dict],
    reset: bool = False,
    log_prefix: str = "SeedKB",
) -> dict[str, Any]:
    """把文档列表灌入 Qdrant（chunk → embedding → upsert），并重建进程内 BM25。

    Args:
        docs: [{doc_id, title, text}] 文档列表（加载逻辑由各脚本自定义）
        metadata_fn: 文档 → chunk 元数据（doc_id/title/source/category/security_group）
        reset: 先按 doc_id 清空再写入（幂等重建）
        log_prefix: 日志前缀（区分 mall/ops）
    """
    qdrant = get_qdrant()
    await qdrant.connect()
    if not qdrant.is_connected:
        logger.warning("[%s] Qdrant 不可用，跳过向量写入（仅输出切分统计）", log_prefix)
        return {"docs": len(docs), "chunks": 0, "vector_written": 0, "bm25_built": 0, "qdrant_ok": False}

    total_chunks = 0
    for doc in docs:
        doc_id = doc["doc_id"]
        if reset:
            await qdrant.delete_by_doc(doc_id)

        chunks = chunk_text(doc["text"], metadata=metadata_fn(doc))
        if not chunks:
            continue
        embeddings = embed_sync([c["text"] for c in chunks])
        if embeddings is None:
            logger.warning("[%s] embedding 失败，跳过 %s", log_prefix, doc_id)
            continue
        ok = await qdrant.upsert(chunks, embeddings)
        logger.info("[%s] %s: %d chunks, upsert=%s", log_prefix, doc_id, len(chunks), ok)
        if ok:
            total_chunks += len(chunks)

    # BM25 内存索引（当前进程）
    bm25_docs = []
    for doc in docs:
        chunks = chunk_text(doc["text"], metadata=metadata_fn(doc))
        bm25_docs.extend(chunks)
    if bm25_docs:
        get_bm25().build(bm25_docs)

    await qdrant.close()
    return {
        "docs": len(docs),
        "chunks": total_chunks,
        "vector_written": total_chunks,
        "bm25_built": len(bm25_docs),
        "qdrant_ok": True,
    }
