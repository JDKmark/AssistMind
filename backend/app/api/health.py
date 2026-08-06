"""健康检查端点。"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.core.infra.langfuse import is_langfuse_enabled

router = APIRouter()
settings = get_settings()


@router.get("/health")
async def health():
    """全链路健康检查。

    Phase 1 仅返回应用状态，Phase 2 起逐步加入依赖检查
    （Qdrant / Redis / PostgreSQL / Langfuse）。
    """
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": "0.1.0",
        "debug": settings.DEBUG,
        "dependencies": {
            # TODO: Phase 2 加入实际依赖健康状态
            "qdrant": "pending",
            "redis": "pending",
            "postgres": "pending",
            "langfuse": "ok" if is_langfuse_enabled() else "disabled",
        },
    }
