"""MCP Client：通过 MCP 协议调用 MCP Server 的工具。

使用 mcp 2.0.0 的 streamable_http_client + ClientSession。
通过 AsyncExitStack 管理 context manager 生命周期，使 session 跨方法复用。

失败降级：连接失败/超时返回 {"error": "MCP Server 不可用"}，不抛异常中断 Agent。
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class MCPClient:
    """MCP 客户端，封装工具调用。

    连接通过 AsyncExitStack 管理 streamable_http_client + ClientSession 两个
    async context manager，使 session 在 connect() 与 close() 之间持久存在。
    """

    def __init__(self, server_url: str | None = None) -> None:
        self.server_url = server_url or settings.MCP_SERVER_URL
        self._session: ClientSession | None = None
        self._stack: contextlib.AsyncExitStack | None = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """连接 MCP Server。

        通过 AsyncExitStack 进入 streamable_http_client 与 ClientSession，
        并调用 initialize() 完成握手。

        成功返回 True，失败返回 False（不抛异常）。
        """
        # 先清理已有连接
        await self.close()

        try:
            self._stack = contextlib.AsyncExitStack()
            await self._stack.__aenter__()

            # 进入 streamable_http_client，获取读写流
            read, write = await self._stack.enter_async_context(
                streamable_http_client(self.server_url)
            )

            # 进入 ClientSession 并初始化
            self._session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()

            self._connected = True
            logger.info("[MCP Client] 连接成功: %s", self.server_url)
            return True
        except Exception as e:
            logger.warning("[MCP Client] 连接失败: %s", e)
            self._connected = False
            self._session = None
            # 清理残留的 stack（可能已部分进入）
            if self._stack is not None:
                try:
                    await self._stack.aclose()
                except Exception as close_err:
                    logger.warning("[MCP Client] 关闭残留连接失败: %s", close_err)
                self._stack = None
            return False

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict | list:
        """调用 MCP 工具。

        Args:
            name: 工具名（search_knowledge / create_ticket / transfer_human / get_ticket_status）
            arguments: 工具参数 dict

        Returns:
            工具返回结果（dict 或 list）

        失败降级：连接未建立/调用超时/工具异常 返回 {"error": "..."}，不抛异常。
        """
        if not self._connected:
            # 尝试重连
            if not await self.connect():
                return {"error": "MCP Server 不可用"}

        try:
            result = await self._session.call_tool(name, arguments)
            return self._parse_tool_result(result)
        except Exception as e:
            logger.warning("[MCP Client] 调用工具 %s 失败: %s", name, e)
            self._connected = False
            return {"error": f"工具 {name} 调用失败: {e}"}

    def _parse_tool_result(self, result: Any) -> dict | list:
        """解析 CallToolResult，提取结构化内容。

        优先使用 structured_content；否则从 content 中提取文本并尝试 JSON 解析。
        """
        # 优先使用 structured_content（MCP 2.0.0 工具返回值的 JSON 表示）
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            return structured

        # 从 content 列表中提取文本
        content = getattr(result, "content", [])
        if not content:
            return {}

        # 单个文本内容：尝试 JSON 解析
        if len(content) == 1:
            text = getattr(content[0], "text", None)
            if text is not None:
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    return {"text": text}

        # 多个内容项：返回文本列表
        texts = []
        for item in content:
            text = getattr(item, "text", None)
            if text is not None:
                texts.append(text)
        return texts if texts else {}

    async def list_tools(self) -> list[dict]:
        """列出可用工具。失败返回空列表。"""
        if not self._connected:
            if not await self.connect():
                return []
        try:
            result = await self._session.list_tools()
            return [
                {"name": t.name, "description": t.description}
                for t in result.tools
            ]
        except Exception as e:
            logger.warning("[MCP Client] 列出工具失败: %s", e)
            return []

    async def close(self) -> None:
        """关闭连接。

        通过 AsyncExitStack 退出 ClientSession 与 streamable_http_client，
        触发各自的 __aexit__ 清理逻辑。
        """
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as e:
                logger.warning("[MCP Client] 关闭连接失败: %s", e)
        self._session = None
        self._stack = None
        self._connected = False


# 单例
_client: MCPClient | None = None


def get_mcp_client() -> MCPClient:
    """获取全局 MCPClient 单例。"""
    global _client
    if _client is None:
        _client = MCPClient()
    return _client
