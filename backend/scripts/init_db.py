"""数据库初始化脚本。

创建所有表 + 插入初始用户。
用法：python scripts/init_db.py
"""

from __future__ import annotations

import asyncio
import logging

from app.core.infra.postgres import async_session, engine
from app.models.user import Base, User
from app.models import ticket as _ticket_model  # noqa: F401 注册到 metadata
from app.models import feedback as _feedback_model  # noqa: F401 注册到 metadata
from app.core.security.auth import hash_password

logger = logging.getLogger(__name__)


async def init_db() -> None:
    """创建表 + 初始用户。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("表创建完成")

    # 初始用户
    async with async_session() as session:
        from sqlalchemy import select

        result = await session.execute(select(User).where(User.username == "admin"))
        if result.scalar_one_or_none() is None:
            session.add(
                User(
                    username="admin",
                    hashed_password=hash_password("admin123"),
                    role="admin",
                )
            )
            session.add(
                User(
                    username="agent",
                    hashed_password=hash_password("agent123"),
                    role="agent",
                )
            )
            session.add(
                User(
                    username="user",
                    hashed_password=hash_password("user123"),
                    role="user",
                )
            )
            await session.commit()
            logger.info("初始用户创建完成：admin/agent/user")
        else:
            logger.info("初始用户已存在，跳过")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
