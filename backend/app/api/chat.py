"""聊天路由：SSE 流式问答。

POST /api/v1/chat/ask
- 接收 {query, history?}，调用三级意图路由，按意图分流：
  - faq → RAGEngine 检索 + 生成
  - task → ToolAgent（通过 MCP 调工具）
  - chat → 直接 LLM 对话
  - unclear → 返回澄清话术
- 用 StreamingResponse + async generator 生成 SSE（text/event-stream）。

SSE 事件类型（7 种）：
  start / retrieving / rewriting / generating / tool_call / tool_result / done / error
注：done 与 error 也参与终止信号，共 8 个事件名，spec 列举 7 类业务事件 + error。

每条事件格式：`event: <name>\ndata: <json>\n\n`
流已开始后无法改 HTTP status code，异常时发送 error 事件并结束流。
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.agents.tool_agent import ToolAgent
from app.api.ops import _diagnose_stream, DiagnoseRequest
from app.core.infra.llm_factory import call_llm
from app.core.rag import engine as rag_engine
from app.core.router.intent import route
from app.schemas.chat import ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter()


# unclear 意图兜底澄清话术
_CLARIFY_ANSWER = (
    "您好，我不太确定您的问题意图。您可以尝试：\n"
    "1. 描述产品功能或文档相关问题（例如“如何配置 XX”）；\n"
    "2. 告诉我需要执行的操作（例如“创建工单”“转人工”）；\n"
    "3. 或者直接和我说说话。\n"
    "请补充更多信息，我会更好地为您服务。"
)

# chat 意图系统提示
_CHAT_SYSTEM = "你是 AssistMind 智能客服，请友好、简洁地与用户对话。"

_SSE_MEDIA_TYPE = "text/event-stream"


def _sse_event(event: str, data: dict[str, Any]) -> str:
    """格式化一条 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _event_stream(req: ChatRequest) -> AsyncIterator[str]:
    """SSE 事件生成器：意图路由 → 分流处理 → 流式返回。

    异常时发送 error 事件并结束流（不抛 500，因为流已开始无法改 status code）。
    """
    try:
        # 1. 意图路由
        route_result = await route(req.query)
        intent = route_result.get("intent", "unclear")

        # 2. start 事件
        yield _sse_event("start", {"query": req.query, "intent": intent})

        history = (
            [h.model_dump() for h in req.history] if req.history else None
        )

        # 3. 按意图分流
        if intent == "faq":
            async for chunk in _handle_faq(req.query, history):
                yield chunk
        elif intent == "task":
            async for chunk in _handle_task(req.query):
                yield chunk
        elif intent == "chat":
            async for chunk in _handle_chat(req.query):
                yield chunk
        elif intent == "diagnose":
            async for chunk in _diagnose_stream(
                DiagnoseRequest(query=req.query), include_start=False
            ):
                yield chunk
        else:  # unclear
            yield _sse_event("done", {"answer": _CLARIFY_ANSWER})
    except Exception as e:
        logger.warning("[Chat] SSE 流处理异常: %s", e)
        yield _sse_event("error", {"message": str(e)})


async def _handle_faq(
    query: str, history: list[dict[str, str]] | None
) -> AsyncIterator[str]:
    """faq 意图：start → retrieving → (rewriting) → generating → done。"""
    yield _sse_event("retrieving", {})

    retrieval = await rag_engine.retrieve(query)

    # 触发了查询改写（有变体且未降级）时通知客户端
    rewrites = retrieval.get("rewrites") or {}
    variants = rewrites.get("variants") or []
    if variants and not rewrites.get("degraded"):
        yield _sse_event("rewriting", {"variants": variants})

    yield _sse_event("generating", {})

    gen = await rag_engine.generate(query, retrieval.get("contexts", []), history)

    yield _sse_event(
        "done",
        {"answer": gen.get("answer", ""), "sources": gen.get("sources", [])},
    )


async def _handle_task(query: str) -> AsyncIterator[str]:
    """task 意图：start → tool_call → tool_result → (多轮) → done。"""
    agent = ToolAgent()
    result = await agent.run(query)

    tool_calls = result.get("tool_calls") or []
    for tc in tool_calls:
        tool_name = tc.get("name", "")
        arguments = tc.get("input") or {}
        yield _sse_event(
            "tool_call", {"tool_name": tool_name, "arguments": arguments}
        )
        yield _sse_event(
            "tool_result",
            {"tool_name": tool_name, "result": tc.get("result")},
        )

    yield _sse_event("done", {"answer": result.get("answer", "")})


async def _handle_chat(query: str) -> AsyncIterator[str]:
    """chat 意图：start → generating → done。"""
    yield _sse_event("generating", {})
    answer = await call_llm(query, system=_CHAT_SYSTEM, generation=True)
    yield _sse_event("done", {"answer": answer})


@router.post("/ask")
async def chat_ask(req: ChatRequest):
    """SSE 流式聊天接口。

    返回 text/event-stream，事件序列见模块文档字符串。
    """
    return StreamingResponse(
        _event_stream(req),
        media_type=_SSE_MEDIA_TYPE,
    )
