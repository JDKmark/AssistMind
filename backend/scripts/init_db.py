"""数据库初始化脚本。

创建所有表 + 插入初始用户。
用法：python scripts/init_db.py
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import bindparam, text

from app.core.infra.postgres import async_session, engine
from app.core.security.auth import hash_password
from app.models import audit as _audit_model  # noqa: F401 注册到 metadata
from app.models import feedback as _feedback_model  # noqa: F401 注册到 metadata
from app.models import mall as _mall_model  # noqa: F401 注册到 metadata（订单/商品/物流/售后表）
from app.models import ticket as _ticket_model  # noqa: F401 注册到 metadata
from app.models.user import Base, User

logger = logging.getLogger(__name__)

# RAG Bad Case 闭环新增反馈字段：create_all 只新建表不补存量表列，需幂等 ALTER
_FEEDBACK_EXTRA_COLUMNS = [
    ("conversation_id", "VARCHAR(64)"),
    ("trace_id", "VARCHAR(64)"),
    ("query", "TEXT"),
    ("answer", "TEXT"),
    ("sources", "TEXT"),
    ("intent", "VARCHAR(16)"),
    ("crag_action", "VARCHAR(16)"),
    ("degraded", "TEXT"),
    ("exported", "BOOLEAN DEFAULT false"),
]


_MALL_EXTRA_COLUMNS = [
    ("owner_username", "VARCHAR(64)"),
]

# 初始账号清单：(username, password, role)，逐账号幂等创建（存在则跳过，不更新）
_INITIAL_USERS = [
    ("admin", "admin123", "admin"),
    ("agent", "agent123", "agent"),
    ("user", "user123", "user"),
    ("user1", "user1123", "user"),
    ("user2", "user2123", "user"),
]

# 存量演示订单归属迁移：001/002 → user1、003/004 → user2。
# 条件带括号限定旧归属（NULL 或 'user'），已迁移过的行不再触碰（幂等）。
_OWNER_MIGRATIONS = [
    (("20240801001", "20240801002"), "user1"),
    (("20240801003", "20240801004"), "user2"),
]


async def init_db() -> None:
    """创建表 + 初始用户 + 存量反馈表补列。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("表创建完成")

    # 存量 users 表补列（幂等，重复执行安全）
    async with engine.begin() as conn:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true")
        )
        await conn.execute(text("UPDATE users SET is_active = true WHERE is_active IS NULL"))
        await conn.execute(text("ALTER TABLE users ALTER COLUMN is_active SET NOT NULL"))
    logger.info("用户启用状态字段补齐完成")

    # 存量 feedbacks 表补列（幂等，重复执行安全）
    async with engine.begin() as conn:
        for col, ddl in _FEEDBACK_EXTRA_COLUMNS:
            await conn.execute(text(f"ALTER TABLE feedbacks ADD COLUMN IF NOT EXISTS {col} {ddl}"))
    logger.info("反馈表 Bad Case 闭环字段补齐完成")

    async with engine.begin() as conn:
        for col, ddl in _MALL_EXTRA_COLUMNS:
            await conn.execute(
                text(f"ALTER TABLE mall_orders ADD COLUMN IF NOT EXISTS {col} {ddl}")
            )
        for order_sns, new_owner in _OWNER_MIGRATIONS:
            await conn.execute(
                text(
                    "UPDATE mall_orders SET owner_username = :owner "
                    "WHERE (owner_username IS NULL OR owner_username = 'user') "
                    "AND order_sn IN :sns"
                ).bindparams(bindparam("sns", expanding=True)),
                {"owner": new_owner, "sns": list(order_sns)},
            )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_mall_orders_owner_username "
                "ON mall_orders (owner_username)"
            )
        )
    logger.info("商城订单归属字段补齐完成")

    # 初始用户：逐账号幂等创建（存在则跳过；部分存在的环境也能补齐缺失账号）
    async with async_session() as session:
        from sqlalchemy import select

        created: list[str] = []
        for username, password, role in _INITIAL_USERS:
            result = await session.execute(select(User).where(User.username == username))
            if result.scalar_one_or_none() is not None:
                continue
            session.add(
                User(
                    username=username,
                    hashed_password=hash_password(password),
                    role=role,
                )
            )
            created.append(username)
        if created:
            await session.commit()
            logger.info("初始用户创建完成：%s", "/".join(created))
        else:
            logger.info("初始用户均已存在，跳过")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
