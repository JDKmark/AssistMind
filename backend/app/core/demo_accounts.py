"""演示账号清单（唯一来源）。

`scripts/init_db.py` 用它建号，`GET /auth/demo-accounts` 用它给登录页做「一键填入」。
口令只存在于本模块，且只在 `DEBUG=true` 时经接口返回——生产环境（DEBUG=false）不对外暴露任何口令。

新增/调整演示账号只改这里，避免前后端各维护一份导致「页面提示的账号登不进去」。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DemoAccount:
    """一个演示账号。

    `listed=False` 表示仅建号、不在登录页展示（保留历史账号，避免破坏既有演示数据归属）。
    """

    username: str
    password: str
    role: str
    label: str
    listed: bool = True


DEMO_ACCOUNTS: tuple[DemoAccount, ...] = (
    DemoAccount("admin", "admin123", "admin", "管理员"),
    DemoAccount("agent", "agent123", "agent", "客服"),
    DemoAccount("user", "user123", "user", "用户", listed=False),
    DemoAccount("user1", "user1123", "user", "用户1"),
    DemoAccount("user2", "user2123", "user", "用户2"),
)


def initial_users() -> list[tuple[str, str, str]]:
    """建号用清单：(username, password, role)。"""
    return [(a.username, a.password, a.role) for a in DEMO_ACCOUNTS]


def listed_accounts() -> list[DemoAccount]:
    """登录页展示用清单。"""
    return [a for a in DEMO_ACCOUNTS if a.listed]
