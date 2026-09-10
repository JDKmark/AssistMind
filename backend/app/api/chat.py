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
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.agents.tool_agent import ToolAgent
from app.api.deps import get_current_user
from app.core.dialog import trim_history
from app.core.infra.langfuse import get_langfuse
from app.core.infra.llm_factory import call_llm
from app.core.mcp.client import MCPClient
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


def _safe_span_update(span: Any, **kwargs: Any) -> None:
    """安全更新 Langfuse span（埋点是旁路逻辑，失败只记日志，不影响主流程）。"""
    try:
        span.update(**kwargs)
    except Exception as e:
        logger.warning("[Langfuse] span 更新失败（忽略，不影响调用）: %s", e)


@contextmanager
def _faq_trace_span(query: str) -> Iterator[Any | None]:
    """包住 faq 会话的 Langfuse 根 trace（未启用时 yield None，全程 no-op）。

    faq 链路里的 llm.call span 通过 OTEL context 自动挂到本 trace 名下，使
    「query → 检索来源 → 生成」形成可按会话归因的证据链；trace_id 经 done
    事件回传前端（提交反馈时关联，bad case 归因/回流用）。与 ops 诊断 trace 同构。
    """
    langfuse = get_langfuse()
    if langfuse is None:
        yield None
        return
    with langfuse.start_as_current_observation(name="chat_faq", input=query) as span:
        try:
            yield span
        except Exception as e:
            _safe_span_update(span, level="ERROR", status_message=str(e)[:2000])
            raise


def _faq_done_event(
    span: Any,
    query: str,
    conversation_id: str,
    answer: str,
    sources: list[dict[str, Any]],
    crag: dict[str, Any],
    degraded: list[str],
) -> str:
    """构造 faq done 事件：写 trace IO/元数据 + 附 trace_id / conversation_id。

    未启用 Langfuse（span 为 None）时跳过埋点，done 事件字段不缺失。
    """
    trace_id = ""
    if span is not None:
        try:
            # langfuse 4.14：trace_id 是 span 属性（get_trace_id 方法不存在）
            trace_id = getattr(span, "trace_id", None) or ""
        except Exception as e:
            logger.warning("[Langfuse] 获取 trace_id 失败（忽略）: %s", e)
        try:
            span.set_trace_io(
                input=query,
                output={"answer": answer, "sources": sources},
            )
        except Exception as e:
            logger.warning("[Langfuse] set_trace_io 失败（忽略，不影响主流程）: %s", e)
        _safe_span_update(
            span,
            metadata={
                "crag_action": crag.get("action", ""),
                "crag_score": crag.get("score"),
                "degraded": degraded,
                "intent": "faq",
                "conversation_id": conversation_id,
            },
        )
    return _sse_event(
        "done",
        {
            "answer": answer,
            "sources": sources,
            "trace_id": trace_id,
            "conversation_id": conversation_id,
            "crag_action": crag.get("action", ""),
            "degraded": degraded,
        },
    )


async def _event_stream(
    req: ChatRequest, role: str = "user", access_token: str | None = None
) -> AsyncIterator[str]:
    """SSE 事件生成器：意图路由 → 分流处理 → 流式返回。

    异常时发送 error 事件并结束流（不抛 500，因为流已开始无法改 status code）。
    """
    try:
        # 1. 意图路由
        route_result = await route(req.query)
        intent = route_result.get("intent", "unclear")

        # 2. start 事件（conversation_id 供前端反馈关联本条问答）
        conversation_id = str(uuid.uuid4())
        yield _sse_event(
            "start",
            {"query": req.query, "intent": intent, "conversation_id": conversation_id},
        )

        history = [h.model_dump() for h in req.history] if req.history else None
        # 记忆窗口裁剪：统一由 DialogManager 处理（chat/faq/task 同语义）
        history = trim_history(history)

        # 3. 按意图分流
        if intent == "faq":
            async for chunk in _handle_faq(
                req.query, history, role=role, conversation_id=conversation_id
            ):
                yield chunk
        elif intent == "task":
            async for chunk in _handle_task(req.query, history, access_token=access_token):
                yield chunk
        elif intent == "chat":
            async for chunk in _handle_chat(req.query, history):
                yield chunk
        else:  # unclear
            yield _sse_event("done", {"answer": _CLARIFY_ANSWER})
    except Exception as e:
        logger.warning("[Chat] SSE 流处理异常: %s", e)
        yield _sse_event("error", {"message": str(e)})


async def _handle_faq(
    query: str,
    history: list[dict[str, str]] | None,
    role: str = "user",
    conversation_id: str = "",
) -> AsyncIterator[str]:
    """faq 意图：retrieving → (rewriting) → generating → done。

    CRAG 决策与 engine.answer 共用同一门禁（should_rewrite_retry / retry_query_for）：
    - 空检索 / no_result → 直接 done(未找到)，不带空上下文生成（防空检索幻觉）
    - 低分 rewrite_retry → 用改写变体二次检索，命中时发 rewriting 事件；重检索仍空则复检
    - 整条链路包在 Langfuse 根 trace（chat_faq）里，done 回传 trace_id / conversation_id
      （bad case 归因时凭 trace_id 还原证据链，提交反馈时关联）
    """
    yield _sse_event("retrieving", {})

    with _faq_trace_span(query) as span:
        retrieval = await rag_engine.retrieve(query, role=role)

        crag = retrieval.get("crag", {})
        degraded = list(retrieval.get("degraded", []))

        # 触发 CRAG 被动改写（有变体且未降级）时通知客户端
        if rag_engine.should_rewrite_retry(retrieval):
            rewrites = retrieval.get("rewrites") or {}
            variants = rewrites.get("variants") or []
            if variants and not rewrites.get("degraded"):
                yield _sse_event("rewriting", {"variants": variants})
            retry_query = rag_engine.retry_query_for(retrieval, query)
            retry_retrieval = await rag_engine.retrieve(retry_query, role=role)
            degraded = degraded + list(retry_retrieval.get("degraded", []))
            crag = retry_retrieval.get("crag", {})
            # P1 复检：重检索后仍空 / 仍 no_result → 走"未找到"，避免错误生成
            if not retry_retrieval.get("contexts") or crag.get("action") == "no_result":
                yield _faq_done_event(
                    span,
                    query,
                    conversation_id,
                    rag_engine.no_result_answer(query),
                    [],
                    crag,
                    degraded,
                )
                return
            contexts = retry_retrieval.get("contexts", [])
        elif crag.get("action") == "no_result":
            yield _faq_done_event(
                span,
                query,
                conversation_id,
                rag_engine.no_result_answer(query),
                [],
                crag,
                degraded,
            )
            return
        else:
            contexts = retrieval.get("contexts", [])

        yield _sse_event("generating", {})

        gen = await rag_engine.generate(query, contexts, history)
        # 生成阶段降级（LLM 模板兜底）并入追溯快照
        degraded = degraded + (["llm"] if gen.get("degraded") else [])

        yield _faq_done_event(
            span,
            query,
            conversation_id,
            gen.get("answer", ""),
            gen.get("sources", []),
            crag,
            degraded,
        )


async def _handle_task(
    query: str, history: list[dict[str, str]] | None = None, access_token: str | None = None
) -> AsyncIterator[str]:
    """task 意图：tool_call → tool_result → (多轮) → done。

    history 传入 ToolAgent：多轮场景（如「查订单 20240801001」→「物流到哪了？」）
    依赖上文订单号，ToolAgent 内部实体识别会从历史回溯补填工具参数。
    """
    client = MCPClient(access_token=access_token)
    agent = ToolAgent(mcp_client=client)
    result = await agent.run(query, history=history)

    tool_calls = result.get("tool_calls") or []
    for tc in tool_calls:
        tool_name = tc.get("name", "")
        arguments = tc.get("input") or {}
        yield _sse_event("tool_call", {"tool_name": tool_name, "arguments": arguments})
        yield _sse_event(
            "tool_result",
            {"tool_name": tool_name, "result": tc.get("result")},
        )

    yield _sse_event("done", {"answer": result.get("answer", "")})


async def _handle_chat(
    query: str, history: list[dict[str, str]] | None = None
) -> AsyncIterator[str]:
    """chat 意图：generating → done（历史拼入 prompt）。

    历史格式与 engine.generate 一致（用户/客服: 内容），按 MEMORY_WINDOW 已裁剪。
    """
    yield _sse_event("generating", {})
    history_text = ""
    if history:
        history_text = "\n".join(
            [
                f"{'用户' if h.get('role') == 'user' else '客服'}: {h.get('content', '')}"
                for h in history
            ]
        )
    prompt = f"对话历史：\n{history_text}\n\n{query}" if history_text else query
    answer = await call_llm(prompt, system=_CHAT_SYSTEM, generation=True)
    yield _sse_event("done", {"answer": answer})


@router.post("/ask")
async def chat_ask(
    req: ChatRequest,
    user: Annotated[dict, Depends(get_current_user)],
):
    """SSE 流式聊天接口（需登录，JWT 校验通过后进入意图路由）。

    返回 text/event-stream，事件序列见模块文档字符串。

    登录用户 role 透传进 faq 检索：Qdrant payload 的 security_group RBAC
    过滤据此生效（user/agent/admin 权限分级）。
    """
    return StreamingResponse(
        _event_stream(req, role=user.get("role", "user"), access_token=user.get("access_token")),
        media_type=_SSE_MEDIA_TYPE,
    )
