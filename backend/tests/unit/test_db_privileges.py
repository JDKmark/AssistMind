"""PostgreSQL 最小权限账号分离单元测试（phase13 Task 7）。

覆盖：
- 配置含 DATABASE_MIGRATION_URL 字段
- _parse_dsn 从 DSN 解析 runtime (username, password, dbname)
- _runtime_grants_sql 只授最小 DML 权限（无 CREATE/ALTER/DROP/TRUNCATE）
- _ensure_runtime_role_sql：同账号跳过；异账号幂等创建 LOGIN 角色（不含密码明文）
- _migration_dsn：显式 migration URL 优先，缺省回退 runtime URL（本地开发）
"""

from __future__ import annotations

from app.config import Settings
from scripts.init_db import (
    _ensure_runtime_role_sql,
    _migration_dsn,
    _parse_dsn,
    _runtime_grants_sql,
)

# ---------- 配置 ----------


def test_settings_has_migration_url_field():
    """配置含 DATABASE_MIGRATION_URL（默认空，本地回退 runtime URL）。"""
    s = Settings(DATABASE_MIGRATION_URL="")
    assert hasattr(s, "DATABASE_MIGRATION_URL")
    assert s.DATABASE_MIGRATION_URL == ""


# ---------- DSN 解析 ----------


def test_parse_dsn_extracts_user_password_db():
    """从 DSN 解析 runtime 账号信息（含 asyncpg 方言前缀）。"""
    user, password, dbname = _parse_dsn(
        "postgresql+asyncpg://runtime_user:secret123@pg:5432/assistmind"
    )
    assert user == "runtime_user"
    assert password == "secret123"
    assert dbname == "assistmind"


def test_migration_dsn_prefers_explicit():
    """显式 DATABASE_MIGRATION_URL 优先于 runtime URL。"""
    s = Settings(
        DATABASE_URL="postgresql+asyncpg://runtime:pass@h:5432/db",
        DATABASE_MIGRATION_URL="postgresql+asyncpg://migration:mpass@h:5432/db",
    )
    assert _migration_dsn(s) == s.DATABASE_MIGRATION_URL


def test_migration_dsn_falls_back_to_runtime():
    """未配置 migration URL：回退 runtime URL（本地开发单账号）。"""
    s = Settings(DATABASE_MIGRATION_URL="")
    assert _migration_dsn(s) == s.DATABASE_URL


# ---------- 最小权限 GRANT ----------


def test_grants_include_only_dml_privileges():
    """runtime 只授 CONNECT/USAGE/表 DML/序列；不含任何 DDL 权限。"""
    sqls = _runtime_grants_sql("runtime_user", "assistmind")
    joined = " ".join(sqls)
    assert "GRANT CONNECT ON DATABASE \"assistmind\" TO \"runtime_user\"" in joined
    assert "GRANT USAGE ON SCHEMA \"public\" TO \"runtime_user\"" in joined
    assert "SELECT, INSERT, UPDATE, DELETE" in joined
    assert "USAGE, SELECT ON ALL SEQUENCES" in joined
    assert "ALTER DEFAULT PRIVILEGES" in joined
    # 不授予 DDL / 危险权限
    for forbidden in (
        "GRANT CREATE",
        "GRANT ALTER",
        "GRANT DROP",
        "GRANT TRUNCATE",
        "GRANT ALL",
    ):
        assert forbidden not in joined


def test_grants_idempotent_statements():
    """GRANT 语句均为幂等可重复执行，不含 DROP/REVOKE。"""
    joined = " ".join(_runtime_grants_sql("u", "db"))
    assert "REVOKE" not in joined
    assert "DROP" not in joined


# ---------- 角色保障 ----------


def test_ensure_role_skipped_when_same_user():
    """migration 与 runtime 同账号（本地开发）：跳过角色创建，避免改动 DB owner 密码。"""
    assert _ensure_runtime_role_sql("postgres", "postgres") is None


def test_ensure_role_creates_runtime_role():
    """异账号：幂等创建 runtime LOGIN 角色，SQL 不含密码明文。"""
    sql = _ensure_runtime_role_sql("postgres", "runtime_user")
    assert sql is not None
    assert "runtime_user" in sql
    assert "CREATE ROLE" in sql
    assert "PASSWORD" not in sql  # 密码经参数绑定，不进 SQL 文本
    assert "postgres" not in sql  # 角色名不带 migration 用户，避免误改


# ---------- 账号分离启动校验（H2：拒绝 postgres 超管 / 缺迁移账号 / 账号复用）----------


def test_db_roles_pass_with_separated_accounts():
    """独立 runtime + 迁移账号：通过。"""
    assert (
        Settings.db_minimal_privilege_error(
            "postgresql+asyncpg://runtime:p@h:5432/db",
            "postgresql+asyncpg://migrator:m@h:5432/db",
        )
        is None
    )


def test_db_roles_reject_runtime_superuser_with_migration():
    """runtime 仍为 postgres 且有迁移账号：拒绝（声称分离但仍用超管）。"""
    err = Settings.db_minimal_privilege_error(
        "postgresql+asyncpg://postgres:postgres@h:5432/db",
        "postgresql+asyncpg://migrator:m@h:5432/db",
    )
    assert err is not None
    assert "postgres" in err


def test_db_roles_ok_local_single_account():
    """本地单账号（runtime=postgres 且无迁移账号）：放行（与旧行为一致）。"""
    assert (
        Settings.db_minimal_privilege_error(
            "postgresql+asyncpg://postgres:postgres@h:5432/db", ""
        )
        is None
    )


def test_db_roles_reject_missing_migration_for_custom_runtime():
    """自定义 runtime 账号缺迁移账号：拒绝。"""
    err = Settings.db_minimal_privilege_error(
        "postgresql+asyncpg://runtime:p@h:5432/db", ""
    )
    assert err is not None
    assert "DATABASE_MIGRATION_URL" in err


def test_db_roles_reject_shared_account():
    """runtime 与迁移账号相同：拒绝。"""
    err = Settings.db_minimal_privilege_error(
        "postgresql+asyncpg://runtime:p@h:5432/db",
        "postgresql+asyncpg://runtime:m@h:5432/db",
    )
    assert err is not None
    assert "分离" in err


def test_db_roles_reject_empty_runtime_username():
    """runtime 缺少用户名：拒绝。"""
    err = Settings.db_minimal_privilege_error(
        "postgresql+asyncpg://@h:5432/db", "postgresql+asyncpg://migrator:m@h:5432/db"
    )
    assert err is not None
    assert "用户名" in err
