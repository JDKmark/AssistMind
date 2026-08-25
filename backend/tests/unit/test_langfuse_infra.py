"""Langfuse 基础设施单元测试。

mock 策略：patch app.core.infra.langfuse.settings 控制启用/未启用，
patch Langfuse 类避免创建真实客户端；不连真实 Langfuse 服务。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.infra import langfuse as langfuse_module
from app.core.infra.langfuse import get_langfuse, is_langfuse_enabled, reset_langfuse


@pytest.fixture(autouse=True)
def reset_langfuse_singleton():
    """每个测试前后重置 Langfuse 单例，避免测试间状态泄漏。"""
    reset_langfuse()
    yield
    reset_langfuse()


def _patch_settings(monkeypatch, **kwargs) -> None:
    """用指定字段替换 langfuse 模块级 settings。

    Langfuse key 未显式传入时强制为 None，避免 `Settings(**kwargs)` 从本机
    `.env` 读入已配置的 key，保证「未配置」用例不受环境变量污染。
    """
    values = {
        "LANGFUSE_PUBLIC_KEY": None,
        "LANGFUSE_SECRET_KEY": None,
    }
    values.update(kwargs)
    monkeypatch.setattr(langfuse_module, "settings", Settings(**values))


def test_is_langfuse_enabled_false_without_keys(monkeypatch):
    """未配置公钥/私钥时 is_langfuse_enabled 返回 False。"""
    _patch_settings(monkeypatch)  # 默认 LANGFUSE_PUBLIC_KEY/SECRET_KEY 均为 None
    assert is_langfuse_enabled() is False


def test_is_langfuse_enabled_false_with_only_public_key(monkeypatch):
    """只配置公钥（缺私钥）时仍视为未启用。"""
    _patch_settings(monkeypatch, LANGFUSE_PUBLIC_KEY="pk-test")
    assert is_langfuse_enabled() is False


def test_is_langfuse_enabled_true_with_both_keys(monkeypatch):
    """公钥与私钥都配置时启用。"""
    _patch_settings(
        monkeypatch,
        LANGFUSE_PUBLIC_KEY="pk-test",
        LANGFUSE_SECRET_KEY="sk-test",
    )
    assert is_langfuse_enabled() is True


def test_get_langfuse_none_when_disabled(monkeypatch):
    """未启用时 get_langfuse 返回 None，且不构造客户端、不抛异常。"""
    _patch_settings(monkeypatch)
    with patch("app.core.infra.langfuse.Langfuse") as mock_langfuse:
        assert get_langfuse() is None
        mock_langfuse.assert_not_called()


def test_get_langfuse_creates_singleton_when_enabled(monkeypatch):
    """启用时惰性创建客户端，用 settings 的 HOST/公钥/私钥构造，且为单例。"""
    _patch_settings(
        monkeypatch,
        LANGFUSE_HOST="http://localhost:3001",
        LANGFUSE_PUBLIC_KEY="pk-test",
        LANGFUSE_SECRET_KEY="sk-test",
    )
    with patch("app.core.infra.langfuse.Langfuse") as mock_langfuse:
        mock_langfuse.return_value = mock_instance = object()
        first = get_langfuse()
        second = get_langfuse()
        assert first is mock_instance
        assert second is mock_instance  # 单例：第二次不重复构造
        mock_langfuse.assert_called_once_with(
            public_key="pk-test",
            secret_key="sk-test",
            base_url="http://localhost:3001",
        )


def test_get_langfuse_returns_none_after_reset(monkeypatch):
    """reset_langfuse 后重新 get 会重建实例（测试隔离用）。"""
    _patch_settings(
        monkeypatch,
        LANGFUSE_PUBLIC_KEY="pk-test",
        LANGFUSE_SECRET_KEY="sk-test",
    )
    with patch("app.core.infra.langfuse.Langfuse") as mock_langfuse:
        mock_langfuse.return_value = "instance-1"
        assert get_langfuse() == "instance-1"
        reset_langfuse()
        mock_langfuse.return_value = "instance-2"
        assert get_langfuse() == "instance-2"
        assert mock_langfuse.call_count == 2


def test_health_langfuse_disabled_when_not_configured(monkeypatch):
    """未配置 LANGFUSE key 时 health 返回 langfuse=disabled，应用可正常导入启动。

    显式将 langfuse settings 的 key 置空，避免读到本机 `.env` 中已配置的 key。
    """
    monkeypatch.setattr(
        langfuse_module,
        "settings",
        Settings(LANGFUSE_PUBLIC_KEY=None, LANGFUSE_SECRET_KEY=None),
    )
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dependencies"]["langfuse"] == "disabled"


def test_health_langfuse_ok_when_configured(monkeypatch):
    """配置 LANGFUSE key 时 health 返回 langfuse=ok。"""
    monkeypatch.setattr("app.api.health.is_langfuse_enabled", lambda: True)
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dependencies"]["langfuse"] == "ok"
