"""MCP 入口安全边界：统一 Bearer 认证 + 身份维度固定窗口限流（phase13 Task 5/6）。

职责：
- 在 FastMCP Streamable HTTP 应用外层统一验证 Bearer JWT：认证成功后才允许
  建立 session、列出工具或调用工具；缺失/无效/过期 token 一律 401，且不进入下游。
- 认证通过后按身份维度（user_id 摘要）做固定窗口限流，使用与普通 HTTP API
  不同的 Redis key scope；超限返回 429 + Retry-After。
- Redis 不可用 → 放行 + warning（不误杀）；认证不因 Redis 状态改变。
- 纯 ASGI 中间件：只处理请求头，响应由下游原样透传（不缓冲 MCP 的 SSE 流）。

角色与 user_id 只取自服务端签发的 token（resolve_token_identity），
工具层（server._requester）再校验一次作为纵深防御。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from app.api.deps import resolve_token_identity
from app.core.infra.rate_limit import check_fixed_window
from app.core.infra.redis import RedisClient

logger = logging.getLogger(__name__)

_UNAUTHORIZED_BODY = json.dumps({"detail": "未认证或认证已过期"}).encode("utf-8")
_TOO_MANY_BODY = json.dumps({"detail": "MCP 请求过于频繁，请稍后再试"}).encode("utf-8")


def identity_digest(identity: str) -> str:
    """身份摘要（不可逆）：Redis key 不存明文 user_id / username / token。"""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


class MCPAuthMiddleware:
    """MCP Streamable HTTP 入口的认证 + 身份维度限流 ASGI 中间件。"""

    def __init__(
        self,
        app,
        *,
        redis: RedisClient,
        limit: int = 60,
        period: int = 60,
        key_prefix: str = "scqa:rl:mcp",
    ):
        self.app = app
        self.redis = redis
        self.limit = limit
        self.period = period
        self.key_prefix = key_prefix

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        token = _extract_bearer(scope)
        if not token:
            return await _send_json(send, 401, _UNAUTHORIZED_BODY)
        try:
            identity = await resolve_token_identity(token)
        except Exception:
            logger.warning("[MCP] 认证失败：token 无效或用户不可用")
            return await _send_json(send, 401, _UNAUTHORIZED_BODY)

        scope_id = identity_digest(identity["user_id"] or identity["username"])
        allowed, _count = await check_fixed_window(
            self.redis,
            key_prefix=self.key_prefix,
            identity=scope_id,
            limit=self.limit,
            period=self.period,
        )
        if not allowed:
            retry_after = self.period - int(time.time()) % self.period
            logger.warning(
                "[MCP] 触发限流 %s（%d 次/%ds）: %s",
                scope.get("path", ""),
                self.limit,
                self.period,
                identity["username"],
            )
            return await _send_json(
                send,
                429,
                _TOO_MANY_BODY,
                headers=[(b"retry-after", str(retry_after).encode("utf-8"))],
            )
        return await self.app(scope, receive, send)


def _extract_bearer(scope) -> str:
    """从 ASGI scope headers 提取 Bearer token（大小写不敏感）。"""
    for key, value in scope.get("headers") or []:
        if key.lower() == b"authorization":
            raw = value.decode("latin-1")
            if raw.startswith("Bearer "):
                return raw[len("Bearer ") :].strip()
    return ""


async def _send_json(send, status: int, body: bytes, headers: list | None = None) -> None:
    resp_headers = [(b"content-type", b"application/json")]
    if headers:
        resp_headers.extend(headers)
    await send({"type": "http.response.start", "status": status, "headers": resp_headers})
    await send({"type": "http.response.body", "body": body})
