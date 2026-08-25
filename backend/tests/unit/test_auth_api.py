"""认证 API 单元测试：登录 / /me / 管理接口角色校验。

覆盖：
- login 成功（签发 JWT + 用户信息）、密码错误 401、未知用户 401
- /me 返回真实登录用户（不再是 placeholder）
- /me 无 token 401
- 知识库 delete/rebuild 仅管理员（user 角色 403）

mock 策略：mock app.api.auth 中的 async_session（不连 PostgreSQL），
JWT 用真实 create_access_token 签发。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.security.auth import create_access_token, hash_password
from app.main import app
from app.models.user import User

client = TestClient(app)

ADMIN_TOKEN = create_access_token({"sub": "admin", "role": "admin"})
USER_TOKEN = create_access_token({"sub": "alice", "role": "user"})
AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


class FakeScalarResult:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class FakeSession:
    def __init__(self, user):
        self._user = user

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return FakeScalarResult(self._user)


def _make_user(username: str = "admin", password: str = "admin123", role: str = "admin") -> User:
    return User(
        username=username,
        hashed_password=hash_password(password),
        role=role,
        is_active=True,
    )


# ---------- login ----------

def test_login_success_returns_token_and_user(monkeypatch):
    """正确密码：返回 JWT + 用户信息（username/role）。"""
    monkeypatch.setattr(
        "app.api.auth.async_session", lambda: FakeSession(_make_user())
    )
    resp = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["access_token"]
    assert data["token_type"] == "bearer"
    assert data["user"] == {"username": "admin", "role": "admin"}


def test_login_inactive_user_403(monkeypatch):
    """密码正确但账号停用：403 且不签发 token。"""
    user = _make_user()
    user.is_active = False
    monkeypatch.setattr("app.api.auth.async_session", lambda: FakeSession(user))

    resp = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "账号已停用"
    assert "access_token" not in resp.json()


def test_user_is_active_column_defaults_to_true():
    """用户启用列在 Python 和数据库侧都默认 true。"""
    column = User.__table__.c.is_active
    assert column.default.arg is True
    assert column.server_default.arg == "true"


def test_login_wrong_password_401(monkeypatch):
    """密码错误：401 且不签发 token。"""
    monkeypatch.setattr(
        "app.api.auth.async_session", lambda: FakeSession(_make_user())
    )
    resp = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert resp.status_code == 401
    assert "access_token" not in resp.json()


def test_login_unknown_user_401(monkeypatch):
    """未知用户：401。"""
    monkeypatch.setattr("app.api.auth.async_session", lambda: FakeSession(None))
    resp = client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "x"}
    )
    assert resp.status_code == 401


# ---------- /me ----------

def test_me_returns_real_user():
    """/me 带合法 token：返回真实用户名与角色（不再是 placeholder）。"""
    resp = client.get("/api/v1/auth/me", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"username": "admin", "role": "admin"}


def test_me_requires_auth():
    """/me 无 token：401。"""
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_me_returns_user_role_token():
    """普通用户角色的 token：/me 返回对应角色。"""
    resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {USER_TOKEN}"},
    )
    assert resp.json() == {"username": "alice", "role": "user"}


# ---------- 管理接口角色校验（knowledge delete/rebuild 仅 admin）----------

def test_knowledge_delete_forbidden_for_user():
    """普通用户调用知识库删除：403（require_admin）。"""
    resp = client.post(
        "/api/v1/knowledge/delete",
        headers={"Authorization": f"Bearer {USER_TOKEN}"},
        json={"doc_id": "d1"},
    )
    assert resp.status_code == 403


def test_knowledge_rebuild_forbidden_for_user():
    """普通用户调用知识库重建：403。"""
    resp = client.post(
        "/api/v1/knowledge/rebuild",
        headers={"Authorization": f"Bearer {USER_TOKEN}"},
    )
    assert resp.status_code == 403


def test_knowledge_rebuild_requires_auth():
    """知识库重建无 token：401（先于角色校验）。"""
    resp = client.post("/api/v1/knowledge/rebuild")
    assert resp.status_code == 401
