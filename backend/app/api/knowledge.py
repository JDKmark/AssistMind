"""知识库管理路由：文档列表 / 上传 / chunk 明细 / 召回测试 / 删除 / 重建索引。

数据源为 Qdrant（向量 + payload 元信息），BM25 内存索引以 Qdrant 全量数据构建。
Qdrant 不可用时按降级规则返回明确错误信息（不抛 500）。
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, field_validator

from app.api.deps import require_admin, require_staff
from app.core.cache.semantic_cache import invalidate as cache_invalidate
from app.core.infra.qdrant import get_qdrant
from app.core.rag.bm25 import get_bm25
from app.core.rag.engine import retrieve_raw
from app.core.tasks import (
    enqueue_task,
    export_badcases,
    ingest_uploaded_document,
    rebuild_knowledge_base,
    run_evaluation,
)

logger = logging.getLogger(__name__)

router = APIRouter()

QDRANT_UNAVAILABLE_MSG = "Qdrant 不可用，知识库操作暂不可用"

# 上传白名单与大小上限（app/api/ → backend/ → 仓库根 knowledge/uploads）
UPLOAD_ALLOWED_EXTS = {".md", ".txt", ".pdf", ".docx", ".sql", ".yml", ".yaml"}
UPLOAD_MAX_BYTES = 5 * 1024 * 1024
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "knowledge", "uploads")


def _enqueue_or_503(func, *args, owner: str = "", **kwargs):
    """入队任务；Redis/队列不可用时返回 503 而非 500（外部调用失败必须有明确降级路径）。"""
    try:
        return enqueue_task(func, *args, owner=owner, **kwargs)
    except Exception as e:
        logger.warning("[Knowledge] 任务入队失败（Redis/队列不可用？）: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="任务队列暂不可用，请稍后重试",
        ) from e


class DeleteDocRequest(BaseModel):
    """删除文档请求。"""

    doc_id: str = Field(..., min_length=1, description="文档 ID")


class SearchTestRequest(BaseModel):
    """召回测试请求（dry-run）。"""

    query: str = Field(..., min_length=1, description="检索问题")
    top_k: int = Field(5, ge=1, le=20, description="返回条数上限")

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query 不能为空白")
        return v


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
    # BM25 增量移除（原地收缩，不再 scroll_all 全量重建）
    try:
        get_bm25().remove_by_doc(req.doc_id)
    except Exception as e:
        logger.warning("[Knowledge] BM25 增量移除失败（不影响删除）: %s", e)
    # 语义缓存失效：已缓存答案可能引用已删文档，必须失效（旁路失败仅 warning）
    try:
        await cache_invalidate()
    except Exception as e:
        logger.warning("[Knowledge] 语义缓存失效失败（不影响删除）: %s", e)
    # 广播 BM25 版本（其他进程惰性重载感知删除；Redis 故障仅 warning）
    try:
        get_bm25().mark_changed()
    except Exception as e:
        logger.warning("[Knowledge] BM25 版本广播失败（不影响删除）: %s", e)
    return {"deleted": True, "doc_id": req.doc_id}


@router.post("/rebuild")
async def rebuild_index(user: Annotated[dict, Depends(require_admin)]):
    """入队重建 BM25 索引任务（仅管理员），秒回 job_id。

    重建为分钟级耗时操作（Qdrant 全量 scroll + BM25 build），同步执行会
    阻塞 HTTP 请求——改为 RQ 异步任务（Retry(max=2)），状态经
    GET /api/v1/jobs/{job_id} 轮询。Qdrant 可用性在入队前快速校验。
    """
    qdrant = get_qdrant()
    if not qdrant.is_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=QDRANT_UNAVAILABLE_MSG,
        )
    job = _enqueue_or_503(rebuild_knowledge_base, owner=user.get("username", ""))
    return {"job_id": job.id, "status": "queued"}


@router.post("/badcases/export")
async def enqueue_badcase_export(user: Annotated[dict, Depends(require_admin)]):
    """入队坏例回流任务（仅管理员），秒回 job_id。

    低分反馈（score<=2）回流为评估集 bad case 属分钟级耗时操作，
    与 rebuild 同模式走 RQ 队列（Retry(max=2)），状态经
    GET /api/v1/jobs/{job_id} 轮询；结果形如 {"exported": n, "marked": n}。
    """
    job = _enqueue_or_503(export_badcases, owner=user.get("username", ""))
    return {"job_id": job.id, "status": "queued"}


@router.post("/evaluate")
async def enqueue_evaluation(user: Annotated[dict, Depends(require_admin)]):
    """入队评估运行任务（仅管理员），秒回 job_id。

    run_eval 依赖 LLM + embedding，子进程隔离执行（run_evaluation 任务），
    状态经 GET /api/v1/jobs/{job_id} 轮询；结果含 ok/returncode/stdout/stderr 摘要。
    """
    job = _enqueue_or_503(run_evaluation, owner=user.get("username", ""))
    return {"job_id": job.id, "status": "queued"}


@router.post("/upload")
async def upload_doc(
    file: UploadFile = File(...),
    category: str = Form(""),
    user: Annotated[dict, Depends(require_admin)] = None,
):
    """上传文档入库（仅管理员）：校验 → 持久化到 knowledge/uploads/ → 入队 RQ 任务。

    入库为分钟级耗时操作（chunk + embedding + upsert + 全量 BM25 重建），
    与 rebuild 同模式走 RQ 异步任务，状态经 GET /api/v1/jobs/{job_id} 轮询。
    校验全部失败即 422 拒绝（不入队、不写文件）；上传白名单外的二进制
    （.exe 等）与超限文件在入口拦截，防刷免费 embedding/存储。
    """
    raw_name = file.filename or ""
    basename = os.path.basename(raw_name.replace("\\", "/"))
    # 路径穿越防御：清洗后为空或原始名含分隔符一律拒绝（../evil.md 不落盘）
    if not basename or basename != raw_name or basename in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="非法文件名：不得包含路径分隔符",
        )
    ext = os.path.splitext(basename.lower())[1]
    if ext not in UPLOAD_ALLOWED_EXTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"不支持的文件类型: {ext or '(无扩展名)'}，"
            f"允许：{', '.join(sorted(UPLOAD_ALLOWED_EXTS))}",
        )
    if (file.size or 0) > UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="文件超过 5MB 上限",
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(UPLOAD_DIR, basename)
    content = await file.read()
    if len(content) > UPLOAD_MAX_BYTES:  # size 缺失时的兜底（读 spool 后判断）
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="文件超过 5MB 上限",
        )
    with open(file_path, "wb") as f:
        f.write(content)

    job = _enqueue_or_503(
        ingest_uploaded_document,
        file_path=file_path,
        filename=basename,
        category=category,
        owner=user.get("username", ""),
    )
    return {"job_id": job.id, "status": "queued"}


@router.get("/{doc_id:path}/chunks")
async def get_doc_chunks(doc_id: str, user: Annotated[dict, Depends(require_staff)] = None):
    """查看文档 chunk 明细（staff）：按 chunk_index 升序返回全文。

    doc_id 用 path 转换器：上传文档 doc_id 形如 uploads/<stem>（含分隔符），
    默认的单段参数匹配不到。scroll_by_doc 为空时回查聚合列表区分
    「存在但无 chunk」（200 空数组）与「文档不存在」（404）；Qdrant 不可用 503。
    """
    qdrant = get_qdrant()
    if not qdrant.is_connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=QDRANT_UNAVAILABLE_MSG,
        )
    chunks = await qdrant.scroll_by_doc(doc_id)
    if not chunks:
        aggregated = await qdrant.scroll_all()
        if not any(d["doc_id"] == doc_id for d in _aggregate_docs(aggregated)):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"文档不存在: {doc_id}",
            )
    return {"doc_id": doc_id, "chunks": chunks}


@router.post("/search-test")
async def search_test(
    req: SearchTestRequest,
    user: Annotated[dict, Depends(require_staff)] = None,
):
    """召回测试 dry-run（staff）：两路召回 + RRF 融合，不含改写/CRAG/重排/生成。

    retrieve_raw 契约保证不抛异常（两路均失败时返回空数组 + degraded 标记），
    RBAC 按请求者角色过滤（Qdrant payload filter security_group）。
    """
    return await retrieve_raw(
        req.query.strip(), role=user.get("role", "user"), top_k=req.top_k
    )
