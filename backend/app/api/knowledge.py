"""知识库管理路由：文档列表 / 删除 / 重建索引。

数据源为 Qdrant（向量 + payload 元信息），BM25 内存索引以 Qdrant 全量数据构建。
Qdrant 不可用时按降级规则返回明确错误信息（不抛 500）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import require_admin, require_staff
from app.core.infra.qdrant import get_qdrant
from app.core.rag.bm25 import get_bm25

router = APIRouter()

QDRANT_UNAVAILABLE_MSG = "Qdrant 不可用，知识库操作暂不可用"


class DeleteDocRequest(BaseModel):
    """删除文档请求。"""

    doc_id: str = Field(..., min_length=1, description="文档 ID")


def _aggregate_docs(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 doc_id 聚合 chunk，统计每个文档的 chunk 数。

    元信息（title/source/category）取该文档首个 chunk 的 payload。
    """
    docs: dict[str, dict[str, Any]] = {}
    for c in chunks:
        doc_id = c.get("doc_id") or ""
        if not doc_id:
            continue
        if doc_id not in docs:
            docs[doc_id] = {
                "doc_id": doc_id,
                "title": c.get("title", ""),
                "source": c.get("source", ""),
                "category": c.get("category", ""),
                "chunk_count": 0,
            }
        docs[doc_id]["chunk_count"] += 1
    return sorted(docs.values(), key=lambda d: d["doc_id"])


@router.get("/list")
async def list_docs(user: Annotated[dict, Depends(require_staff)]):
    """列出知识库文档（按 doc_id 聚合 chunk 数，仅 agent/admin）。

    Qdrant 不可用时返回 200 + error 字段（前端展示降级提示），不抛 500。
    """
    qdrant = get_qdrant()
    if not qdrant.is_connected:
        return {"docs": [], "total": 0, "error": QDRANT_UNAVAILABLE_MSG}
    chunks = await qdrant.scroll_all()
    docs = _aggregate_docs(chunks)
    return {"docs": docs, "total": len(docs)}


@router.post("/delete")
async def delete_doc(
    req: DeleteDocRequest,
    user: Annotated[dict, Depends(require_admin)],
):
    """删除文档（含所有 chunk）并重建 BM25 内存索引（仅管理员）。"""
    qdrant = get_qdrant()
    if not qdrant.is_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=QDRANT_UNAVAILABLE_MSG,
        )
    deleted = await qdrant.delete_by_doc(req.doc_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="删除失败：Qdrant 断路器 Open 或删除异常",
        )
    # 删除后重建 BM25 索引（以剩余 chunk 全量重建）
    remaining = await qdrant.scroll_all()
    get_bm25().build(remaining)
    return {"deleted": True, "doc_id": req.doc_id}


@router.post("/rebuild")
async def rebuild_index(user: Annotated[dict, Depends(require_admin)]):
    """从 Qdrant 全量数据重建 BM25 索引（仅管理员）。"""
    qdrant = get_qdrant()
    if not qdrant.is_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=QDRANT_UNAVAILABLE_MSG,
        )
    chunks = await qdrant.scroll_all()
    get_bm25().build(chunks)
    return {"rebuilt": True, "chunks": len(chunks)}
