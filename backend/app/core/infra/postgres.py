"""PostgreSQL 异步引擎与会话。

TODO: Phase 2 实现完整连接池管理。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

# TODO: Phase 2 在 lifespan 中初始化
engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, pool_pre_ping=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """获取数据库会话（FastAPI 依赖注入）。"""
    async with async_session() as session:
        yield session
