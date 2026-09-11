"""异步任务状态路由：RQ job 查询（staff 可见）。

配合 app.core.tasks（RQ 队列）与 knowledge rebuild 入队改造：
前端提交耗时操作后拿 job_id，轮询本接口显示状态。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_staff
from app.core.tasks import fetch_job

router = APIRouter()

# 错误摘要上限：仅透出 traceback 末行（异常类型 + 消息），不暴露内部栈帧文件路径
_ERROR_MAX_LEN = 500


def _error_summary(exc_info: str | None) -> str:
    """提取 traceback 最后一行非空内容作为错误摘要。"""
    if not exc_info:
        return ""
    lines = [line.strip() for line in exc_info.strip().splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:_ERROR_MAX_LEN]


@router.get("/{job_id}")
async def get_job_status(job_id: str, user: Annotated[dict, Depends(require_staff)]):
    """查询异步任务状态（queued/started/finished/failed）与结果/错误摘要。

    归属校验：仅入队者本人与 admin 可查（job.meta.owner 由 enqueue_task 记录；
    result 含评估 stdout 等敏感明细，agent 不可读他人 admin 入队的任务）。
    非本人/无归属信息与不存在统一 404，防任务 id 枚举。
    """
    job = fetch_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在或已过期",
        )
    owner = (job.get_meta() or {}).get("owner", "")
    if user.get("role") != "admin" and owner != user.get("username", ""):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在或已过期",
        )
    job_status = job.get_status()
    resp = {"job_id": job.id, "status": job_status}
    if job_status == "finished":
        resp["result"] = job.return_value()  # RQ 2.x：return_value 是方法（result 已弃用）
    elif job_status == "failed":
        resp["error"] = _error_summary(job.exc_info)
    return resp
