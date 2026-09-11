"""MCP Client：通过 MCP 协议调用 MCP Server 的工具。

使用 mcp 2.0.0 的 streamable_http_client + ClientSession。
通过 AsyncExitStack 管理 context manager 生命周期，使 session 跨方法复用。

失败降级：连接失败/超时返回 {"error": "MCP Server 不可用"}，不抛异常中断 Agent。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import httpx
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

    def __init__(self, server_url: str | None = None, access_token: str | None = None) -> None:
        self.server_url = server_url or settings.MCP_SERVER_URL
        # 规范化尾斜杠：FastAPI app.mount('/mcp', ...) 对无尾斜杠路径返回 307，
        # mcp 库的 streamable_http_client 不跟随重定向，故统一补全避免连接失败。
        if self.server_url and not self.server_url.endswith("/"):
            self.server_url += "/"
        self.access_token = access_token
        self._session: ClientSession | None = None
        self._stack: contextlib.AsyncExitStack | None = None
        self._connected = False
        # 建连任务（anyio 任务亲和性）：streamable_http_client 的后台任务与
        # cancel scope 绑定在建连任务上，跨任务复用 session 会触发
        # "Attempted to exit a cancel scope" 错乱（chat 链路中 connect 在 SSE
        # 包装任务、execute_tool 在 LangGraph 节点任务，必然跨任务）
        self._owner_task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """连接 MCP Server（建连任务 = 当前任务，供同任务复用场景）。

        通过 AsyncExitStack 进入 streamable_http_client 与 ClientSession，
        并调用 initialize() 完成握手。成功返回 True，失败返回 False（不抛异常）。
        """
        ok = await self._connect_impl()
        if ok:
            self._owner_task = asyncio.current_task()
        return ok

    async def _connect_impl(self) -> bool:
        """连接 MCP Server 的裸实现（不记录属主任务）。"""
        # 先清理已有连接
        await self._close_impl()

        try:
            self._stack = contextlib.AsyncExitStack()
            await self._stack.__aenter__()

            headers = {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}
            # httpx 默认读超时仅 5s：会掐断慢工具调用（search_knowledge CPU 检索
            # 可达 20s+）并反复断开长驻 GET 事件流（断连触发服务端会话终止连锁）。
            # 显式放宽：读 300s / 连接 10s。
            timeout = httpx.Timeout(300.0, connect=10.0)
            http_client = await self._stack.enter_async_context(
                httpx.AsyncClient(headers=headers, timeout=timeout)
            )
            read, write = await self._stack.enter_async_context(
                streamable_http_client(self.server_url, http_client=http_client)
            )

            # 进入 ClientSession 并初始化
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
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

    def _same_task_session(self) -> bool:
        """当前任务是否可直接复用既有 session（建连任务自身）。"""
        return (
            self._connected
            and self._session is not None
            and (self._owner_task is None or self._owner_task is asyncio.current_task())
        )

    async def _one_shot_call(self, name: str, arguments: dict[str, Any]) -> dict | list:
        """跨任务调用：在一次性任务内完成 连接→调用→关闭 全生命周期。

        streamable_http_client 的 anyio task group 有任务亲和性（GET 流与
        cancel scope 绑定建连任务），LangGraph 节点任务跨任务复用 session
        必触发 cancel scope 错乱——在单个新任务内跑完整生命周期则天然满足。
        代价是每次调用一次握手（本地 ~100ms，远小于 LLM/检索延迟）。
        """

        async def _run() -> dict | list:
            client = MCPClient(self.server_url, self.access_token)
            if not await client._connect_impl():
                return {"error": "MCP Server 不可用"}
            try:
                result = await client._session.call_tool(name, arguments)
                return client._parse_tool_result(result)
            except Exception as e:
                logger.warning("[MCP Client] 调用工具 %s 失败: %s", name, e)
                return {"error": f"工具 {name} 调用失败: {e}"}
            finally:
                await client._close_impl()

        return await asyncio.create_task(_run())

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict | list:
        """调用 MCP 工具。

        Args:
            name: 工具名（search_knowledge / create_ticket / transfer_human / get_ticket_status）
            arguments: 工具参数 dict

        Returns:
            工具返回结果（dict 或 list）

        失败降级：连接未建立/调用超时/工具异常 返回 {"error": "..."}，不抛异常。
        """
        if not self._same_task_session():
            if self._connected:
                # session 存在但属主是其他任务：跨任务一次性调用（anyio 任务亲和性，
                # 见 _one_shot_call；不可跨任务复用也不可跨任务关闭旧 stack）
                return await self._one_shot_call(name, arguments)
            if not (await self.connect()):
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
        if not self._same_task_session():
            if self._connected:
                # session 属主是其他任务：跨任务一次性调用
                return await self._one_shot_list_tools()
            if not (await self.connect()):
                return []
        try:
            result = await self._session.list_tools()
            return [{"name": t.name, "description": t.description} for t in result.tools]
        except Exception as e:
            logger.warning("[MCP Client] 列出工具失败: %s", e)
            return []

    async def _one_shot_list_tools(self) -> list[dict]:
        """跨任务列出工具（一次性任务内全生命周期，见 _one_shot_call）。"""

        async def _run() -> list[dict]:
            client = MCPClient(self.server_url, self.access_token)
            if not await client._connect_impl():
                return []
            try:
                result = await client._session.list_tools()
                return [{"name": t.name, "description": t.description} for t in result.tools]
            except Exception as e:
                logger.warning("[MCP Client] 列出工具失败: %s", e)
                return []
            finally:
                await client._close_impl()

        return await asyncio.create_task(_run())

    async def close(self) -> None:
        """关闭连接。

        通过 AsyncExitStack 退出 ClientSession 与 streamable_http_client，
        触发各自的 __aexit__ 清理逻辑。
        """
        await self._close_impl()
        self._owner_task = None

    async def _close_impl(self) -> None:
        """关闭连接的裸实现（不清理属主任务标记）。"""
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
