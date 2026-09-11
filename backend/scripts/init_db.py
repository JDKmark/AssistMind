"""数据库初始化脚本。

创建所有表 + 插入初始用户 + 为 runtime 账号授予最小权限。
用法：python scripts/init_db.py

phase13 最小权限：DDL/回填/GRANT 使用 DATABASE_MIGRATION_URL（迁移账号），
Uvicorn 运行时与 seed 脚本继续使用 DATABASE_URL（runtime 账号，仅 DML）。
本地开发未配置 migration URL 时回退 DATABASE_URL（单账号），行为与旧版一致。
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import bindparam, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.core.security.auth import hash_password
from app.models import audit as _audit_model  # noqa: F401 注册到 metadata
from app.models import conversation as _conversation_model  # noqa: F401 注册到 metadata（conversations/chat_messages）
from app.models import feedback as _feedback_model  # noqa: F401 注册到 metadata
from app.models import mall as _mall_model  # noqa: F401 注册到 metadata（订单/商品/物流/售后表）
from app.models import ticket as _ticket_model  # noqa: F401 注册到 metadata
from app.models.user import Base, User

logger = logging.getLogger(__name__)
settings = get_settings()

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
    ("owner_user_id", "VARCHAR(36)"),
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
    (("20260801001", "20260801002"), "user1"),
    (("20260801003", "20260801004"), "user2"),
]


def _migration_dsn(cfg) -> str:
    """迁移 DSN：显式 DATABASE_MIGRATION_URL 优先；缺省回退 runtime URL（本地单账号）。"""
    return cfg.DATABASE_MIGRATION_URL or cfg.DATABASE_URL


def _parse_dsn(database_url: str) -> tuple[str, str, str]:
    """从 DSN 解析 (username, password, dbname)，供 runtime 角色保障与授权使用。"""
    url = make_url(database_url)
    return url.username or "", url.password or "", url.database or ""


def _runtime_grants_sql(runtime_user: str, dbname: str, schema: str = "public") -> list[str]:
    """runtime 角色最小权限 GRANT（幂等，可重复执行）。

    仅 CONNECT / schema USAGE / 表 DML / 序列 USAGE+SELECT + 默认权限；
    不授予任何 DDL（CREATE/ALTER/DROP/TRUNCATE）或 ALL。
    """
    return [
        f'GRANT CONNECT ON DATABASE "{dbname}" TO "{runtime_user}"',
        f'GRANT USAGE ON SCHEMA "{schema}" TO "{runtime_user}"',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{runtime_user}"',
        f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{runtime_user}"',
        (
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{runtime_user}"'
        ),
        (
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
            f'GRANT USAGE, SELECT ON SEQUENCES TO "{runtime_user}"'
        ),
    ]


def _ensure_runtime_role_sql(migration_user: str, runtime_user: str) -> str | None:
    """保障 runtime LOGIN 角色存在（幂等 DO 块）。

    同账号（本地单账号开发）返回 None，避免改动 DB owner 的密码；
    异账号创建 LOGIN 角色，密码经参数绑定单独设置，不进 SQL 文本。
    """
    if migration_user == runtime_user:
        return None
    return (
        f"DO $role$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{runtime_user}') "
        f"THEN CREATE ROLE \"{runtime_user}\" WITH LOGIN; END IF; END $role$;"
    )


def _plan_owner_backfill(
    users: list[tuple[str, str]],  # [(username, user_id)]
    orders: list[tuple[str, str | None]],  # [(order_sn, owner_username)]，仅待回填行
) -> tuple[list[tuple[str, str]], int]:
    """规划 owner_user_id 回填：返回 [(order_sn, user_id)] 与孤儿订单数。

    输入 orders 应为「owner_user_id IS NULL 且 owner_username 非空」的待回填行
    （幂等由上层 SQL WHERE 保证）；owner_username 无对应用户的订单计为孤儿，
    不分配任何 user_id，由调用方 warning 提示。
    """
    uid_by_username = dict(users)
    pending: list[tuple[str, str]] = []
    orphans = 0
    for order_sn, owner_username in orders:
        if not owner_username:
            continue
        user_id = uid_by_username.get(owner_username)
        if user_id is None:
            orphans += 1
            continue
        pending.append((order_sn, user_id))
    return pending, orphans


async def _backfill_owner_user_id(conn) -> int:
    """按 users.username 幂等回填 mall_orders.owner_user_id，返回孤儿订单数。

    只处理 owner_user_id 为空的行；已回填行不被触碰（可重复执行）。
    """
    user_rows = (await conn.execute(text("SELECT id, username FROM users"))).all()
    users = [(username, user_id) for user_id, username in user_rows]
    order_rows = (
        await conn.execute(
            text(
                "SELECT order_sn, owner_username FROM mall_orders "
                "WHERE owner_user_id IS NULL AND owner_username IS NOT NULL"
            )
        )
    ).all()
    pending, orphans = _plan_owner_backfill(
        users, [(order_sn, owner_username) for order_sn, owner_username in order_rows]
    )
    if pending:
        await conn.execute(
            text("UPDATE mall_orders SET owner_user_id = :uid WHERE order_sn = :sn"),
            [{"uid": user_id, "sn": order_sn} for order_sn, user_id in pending],
        )
    return orphans


async def init_db() -> None:
    """建表 + 初始用户 + 存量补列/回填 + runtime 最小权限授权（幂等，可重复执行）。

    DDL 与授权走迁移账号（DATABASE_MIGRATION_URL，缺省回退 DATABASE_URL），
    runtime 账号（DATABASE_URL）仅被授予业务 DML 权限，不拥有 DDL。
    """
    migration_url = _migration_dsn(settings)
    runtime_user, runtime_password, runtime_db = _parse_dsn(settings.DATABASE_URL)
    migration_user, _mp, _md = _parse_dsn(migration_url)

    migration_engine = create_async_engine(migration_url, pool_pre_ping=True)
    migration_session = async_sessionmaker(
        migration_engine, expire_on_commit=False
    )
    try:
        # ---- 1. DDL：建表 + 补列 + 回填 + 索引（迁移账号）----
        async with migration_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true"
                )
            )
            await conn.execute(text("UPDATE users SET is_active = true WHERE is_active IS NULL"))
            await conn.execute(text("ALTER TABLE users ALTER COLUMN is_active SET NOT NULL"))
            for col, ddl in _FEEDBACK_EXTRA_COLUMNS:
                await conn.execute(
                    text(f"ALTER TABLE feedbacks ADD COLUMN IF NOT EXISTS {col} {ddl}")
                )
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
            orphans = await _backfill_owner_user_id(conn)
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_mall_orders_owner_user_id "
                    "ON mall_orders (owner_user_id)"
                )
            )
        logger.info("表创建 / 字段补齐 / 归属回填完成")
        if orphans:
            logger.warning(
                "商城订单归属回填：%s 条订单 owner_username 未匹配到用户，owner_user_id 保持为空",
                orphans,
            )
        # 迁移观测：报告回填后仍为空的 owner_user_id 记录数（判断何时可安全移除 owner_username）
        async with migration_engine.connect() as conn:
            still_null = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM mall_orders "
                        "WHERE owner_user_id IS NULL AND owner_username IS NOT NULL"
                    )
                )
            ).scalar_one()
        if still_null:
            logger.warning(
                "迁移观测：mall_orders 仍有 %s 条记录 owner_user_id 为空（owner_username 兼容窗口活跃）",
                still_null,
            )
        else:
            logger.info("迁移观测：mall_orders 无 owner_user_id 为空记录，可评估后续移除 owner_username")

        # ---- 2. runtime 账号最小权限（迁移账号执行）----
        role_sql = _ensure_runtime_role_sql(migration_user, runtime_user)
        async with migration_engine.begin() as conn:
            if role_sql is not None:
                await conn.execute(text(role_sql))
                if runtime_password:
                    await conn.execute(
                        text(f'ALTER ROLE "{runtime_user}" WITH PASSWORD :pwd'),
                        {"pwd": runtime_password},
                    )
                logger.info("runtime 角色保障完成：%s", runtime_user)
            for grant_sql in _runtime_grants_sql(runtime_user, runtime_db):
                await conn.execute(text(grant_sql))
        logger.info("runtime 最小权限授权完成：%s", runtime_user)

        # ---- 3. 初始用户（DML）----
        async with migration_session() as session:
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
    finally:
        await migration_engine.dispose()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await init_db()


if __name__ == "__main__":
    asyncio.run(main())
