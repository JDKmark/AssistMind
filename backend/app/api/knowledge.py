"""知识库管理路由。

TODO: Phase 2 实现文档上传 / 列表 / 删除 / 重建索引。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/list")
async def list_docs():
    """列出知识库文档。TODO: Phase 2。"""
    return {"docs": [], "total": 0}
