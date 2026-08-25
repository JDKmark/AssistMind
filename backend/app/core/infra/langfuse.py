"""Langfuse 客户端：LLM 可观测性 / 追踪（模块级单例 + 惰性创建）。

Langfuse SDK 4.14 客户端构造结论（依据 venv site-packages/langfuse/_client/client.py）：
- 构造签名 `Langfuse(*, public_key=None, secret_key=None, base_url=None, host=None, ...)`，
  全部为关键字参数；`host` 已弃用（Deprecated. Use base_url instead），本项目传 `base_url`
- 未传 public_key / secret_key 时**不抛异常**：打 warning 日志
  （"Authentication error: Langfuse client initialized without public_key. "
  "Client will be disabled."）
  并将 tracer 置为 NoOpTracer，客户端进入禁用状态（不发送任何数据）
- 客户端创建不发起网络请求（事件在后台线程批量 flush），构造本身无 I/O
- SDK 默认读环境变量 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL / LANGFUSE_HOST，
  但 pydantic-settings 从 .env 加载后不会回写 os.environ，因此必须用 settings 显式传参

本模块约定：LANGFUSE_PUBLIC_KEY 与 LANGFUSE_SECRET_KEY 任一未配置即视为未启用，
get_langfuse() 返回 None，不构造客户端、不抛异常，调用方降级为"不埋点"。
"""

from __future__ import annotations

import logging

from langfuse import Langfuse

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_langfuse: Langfuse | None = None


def is_langfuse_enabled() -> bool:
    """Langfuse 是否启用：公钥与私钥都配置了才启用。"""
    return bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)


def get_langfuse() -> Langfuse | None:
    """获取 Langfuse 客户端单例（惰性创建）。

    未启用时返回 None：不构造客户端、不抛异常，调用方降级为不埋点。
    """
    global _langfuse
    if not is_langfuse_enabled():
        return None
    if _langfuse is None:
        _langfuse = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            base_url=settings.LANGFUSE_HOST,
        )
        logger.info("[Langfuse] 客户端已创建，host=%s", settings.LANGFUSE_HOST)
    return _langfuse


def reset_langfuse() -> None:
    """重置单例（测试隔离用）。"""
    global _langfuse
    _langfuse = None
