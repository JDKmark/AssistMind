"""聊天路由：SSE 流式问答。

POST /api/v1/chat/ask
- 接收 {query, history?}，调用三级意图路由，按意图分流：
  - faq → RAGEngine 检索 + 生成
  - task → ToolAgent（通过 MCP 调工具）
  - chat → 直接 LLM 对话
  - unclear → 返回澄清话术
- 用 StreamingResponse + async generator 生成 SSE（text/event-stream）。

SSE 事件类型：
  start / disambiguation / retrieving / rewriting / generating / delta / tool_call /
  tool_result / done / error
- disambiguation：产品消歧决策（phase15，仅 faq/task 意图且未直通时发出，位于 start 之后）
- delta：生成阶段逐 chunk 文本（前端打字机效果；done 仍带完整 answer 兜底）
- done 与 error 也参与终止信号，共 10 个事件名。

每条事件格式：`event: <name>\ndata: <json>\n\n`
流已开始后无法改 HTTP status code，异常时发送 error 事件并结束流。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.agents.tool_agent import DEFAULT_SYSTEM_PROMPT, TASK_PERSONA_GUARD, ToolAgent
from app.api.deps import get_current_user
from app.core.cache import semantic_cache
from app.core.conversation_service import save_round
from app.core.dialog import trim_history
from app.core.infra import metrics
from app.core.infra.langfuse import get_langfuse
from app.core.infra.llm_factory import LLMUnavailableError, stream_llm
from app.core.mall.product_disambiguator import disambiguate_product
from app.core.mcp.client import MCPClient
from app.core.personas import emotion_override, list_personas, persona_prompt
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

# resolved 消歧的确认指令（faq/task 两链路 system 追加）：回答开头简短确认产品，
# 用户否定时询问具体款式——「静默消歧 + 顺带确认」话术约束
_DISAMBIG_CONFIRM_SUFFIX = (
    "产品消歧确认：本轮已识别用户所指产品为「{product_name}」，回答开头用一句话简短确认"
    "（例如「您说的是{product_name}吧」）；若用户表示不是该产品，请询问具体款式后再回答。"
)


def _disambig_confirm_suffix(disambiguation: dict | None) -> str | None:
    """resolved 消歧 → 确认指令文本；pass/clarify/None → None（不注入）。"""
    if not disambiguation or disambiguation.get("status") != "resolved":
        return None
    product_name = disambiguation.get("product_name") or ""
    if not product_name:
        return None
    return _DISAMBIG_CONFIRM_SUFFIX.replace("{product_name}", product_name)


def _disambig_candidates_payload(disambiguation: dict) -> list[dict]:
    """clarify 候选的对外载荷（SSE 事件与 done 共用）：仅 product_id/name/spec。"""
    return [
        {
            "product_id": c.get("product_id"),
            "name": c.get("name"),
            "spec": c.get("spec"),
        }
        for c in disambiguation.get("candidates") or []
    ]


def _sse_event(event: str, data: dict[str, Any]) -> str:
    """格式化一条 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _compose_system(*parts: str | None) -> str:
    """拼接 system prompt 各段（跳过空段），统一注入顺序：
    既有 system → 情绪安全阀 override → persona 语气指令（→ task 链路护栏）。

    人格只追加在既有职责之后，不覆盖客服职责与 RAG 事实性约束；
    情绪安全阀在 persona 之前（命中时人格让位）。
    """
    return "\n".join(p for p in parts if p)


def _metric(fn: Any, *args: Any) -> None:
    """指标写入的二次兜底（旁路硬要求）。

    metrics 模块内部已自兜底，但为满足「任何情况下埋点都不得影响请求」，
    调用点再包一层：即使指标函数本身被替换/抛异常，也只 debug 记录。
    """
    try:
        fn(*args)
    except Exception as e:  # pragma: no cover - 指标异常不得影响请求
        logger.debug("[Perf] 指标写入失败（忽略）: %s", e)


def _record_degradation(degraded: list[str] | None) -> None:
    """把降级项列表映射为 assistmind_degradation_total（旁路，失败忽略）。

    component = 降级项名（query_rewrite/embedding/qdrant/bm25/reranker/llm），
    reason 固定 fallback（表示走了对应降级路径）。
    """
    for item in degraded or []:
        _metric(metrics.inc_degradation, str(item), "fallback")


def _emit_perf(
    *,
    intent: str,
    t0: float,
    t_first_delta: float | None,
    t_done: float,
    stage_ms: dict[str, int] | None,
) -> None:
    """输出一轮请求的性能日志 + 指标（T3，严格旁路）。

    - 字段统一 ``*_ms`` 整数：``ttft_ms`` / ``total_ms`` / 各阶段 ``*_ms``
    - 无流式增量（缓存命中 / 澄清 / 工具直答）时 ``ttft_ms=-1``（无首 token 概念）
    - 任何异常都被吞掉（debug），绝不影响请求（旁路性硬要求）
    """
    try:
        total_s = max(0.0, t_done - t0)
        total_ms = round(total_s * 1000)
        ttft_ms = round((t_first_delta - t0) * 1000) if t_first_delta else -1
        metrics.observe_request(intent, total_s)
        if t_first_delta:
            metrics.observe_ttft(intent, max(0.0, t_first_delta - t0))
        payload: dict[str, Any] = {
            "event": "chat_perf",
            "intent": intent,
            "ttft_ms": ttft_ms,
            "total_ms": total_ms,
        }
        for key, value in (stage_ms or {}).items():
            payload[key] = int(value)
        seen = " ".join(
            f"{k}={v}"
            for k, v in payload.items()
            if k.endswith("_ms") and k not in ("ttft_ms", "total_ms")
        )
        logger.info(
            "[Perf] intent=%s ttft_ms=%d total_ms=%d %s",
            intent,
            ttft_ms,
            total_ms,
            seen,
            extra=payload,
        )
    except Exception as e:  # pragma: no cover - 埋点异常不得影响请求
        logger.debug("[Perf] 性能日志输出失败（忽略）: %s", e)


def _merge_retrieval_stage_ms(stage_ms: dict[str, int], retrieval: dict[str, Any]) -> None:
    """合并 engine.retrieve 的 stage_ms（recall_ms 为并行召回段，区别于整段检索）。"""
    src = retrieval.get("stage_ms") or {}
    for key, value in src.items():
        out_key = "recall_ms" if key == "retrieve_ms" else key
        stage_ms[out_key] = int(value)


async def _persist_round_safe(
    conversation_id: str,
    user_id: str,
    query: str,
    answer: str,
    intent: str,
) -> None:
    """done 路径持久化一轮问答（旁路逻辑）。

    持久化失败（PG 故障等）只记 warning，不影响 SSE 响应——spec 硬性降级要求。
    """
    try:
        await save_round(conversation_id, user_id, query, answer, intent=intent)
    except Exception:
        logger.warning("[Chat] 会话持久化失败（不影响响应）", exc_info=True)


def _safe_span_update(span: Any, **kwargs: Any) -> None:
    """安全更新 Langfuse span（埋点是旁路逻辑，失败只记日志，不影响主流程）。"""
    try:
        span.update(**kwargs)
    except Exception as e:
        logger.warning("[Langfuse] span 更新失败（忽略，不影响调用）: %s", e)


@contextmanager
def _chat_trace_span(span_name: str, query: str) -> Iterator[Any | None]:
    """包住一轮会话的 Langfuse 根 trace（未启用时 yield None，全程 no-op）。

    faq/task/chat 三类意图共用：链路里的 llm.call span 通过 OTEL context 自动挂到
    本 trace 名下，使「query → 检索/工具 → 生成」形成可按会话归因的证据链；
    trace_id 经 done 事件回传前端（提交反馈时关联，bad case 归因/回流用）。
    与 ops 诊断 trace 同构。
    """
    langfuse = get_langfuse()
    if langfuse is None:
        yield None
        return
    with langfuse.start_as_current_observation(name=span_name, input=query) as span:
        try:
            yield span
        except Exception as e:
            _safe_span_update(span, level="ERROR", status_message=str(e)[:2000])
            raise


def _span_trace_id(span: Any) -> str:
    """安全读取 span.trace_id（langfuse 4.14 为属性；失败返回空串，不影响主流程）。"""
    if span is None:
        return ""
    try:
        return getattr(span, "trace_id", None) or ""
    except Exception as e:
        logger.warning("[Langfuse] 获取 trace_id 失败（忽略）: %s", e)
        return ""


def _faq_done_event(
    span: Any,
    query: str,
    conversation_id: str,
    answer: str,
    sources: list[dict[str, Any]],
    crag: dict[str, Any],
    degraded: list[str],
    timings: dict[str, int] | None = None,
    from_cache: str = "",
    emotion_override_hit: bool = False,
) -> str:
    """构造 faq done 事件：写 trace IO/元数据 + 附 trace_id / conversation_id。

    未启用 Langfuse（span 为 None）时跳过埋点，done 事件字段不缺失。
    timings 为后端精确计时的阶段耗时（检索 ms / 生成 ms / 总 ms），
    管理员诊断面板据此定位慢环节（网络层耗时不可靠）。
    from_cache 为语义缓存命中来源（L1/L2）——缓存命中同样回传 trace_id，
    避免管理员/客服看到空 Trace ID 误以为追踪失效。
    emotion_override_hit：本轮命中情绪安全阀时在 span metadata 记
    emotion_override=True（未启用 Langfuse 时随 span 为 None 自然 no-op）。
    """
    # 降级触发计数（旁路指标）：每个 faq done 路径都会带 degraded 快照，此处统一上报
    _record_degradation(degraded)
    trace_id = _span_trace_id(span)
    if span is not None:
        try:
            span.set_trace_io(
                input=query,
                output={"answer": answer, "sources": sources},
            )
        except Exception as e:
            logger.warning("[Langfuse] set_trace_io 失败（忽略，不影响主流程）: %s", e)
        metadata = {
            "crag_action": crag.get("action", ""),
            "crag_score": crag.get("score"),
            "degraded": degraded,
            "intent": "faq",
            "conversation_id": conversation_id,
            "timings": timings or {},
            "from_cache": from_cache,
        }
        if emotion_override_hit:
            metadata["emotion_override"] = True
        _safe_span_update(span, metadata=metadata)
    return _sse_event(
        "done",
        {
            "answer": answer,
            "sources": sources,
            "trace_id": trace_id,
            "conversation_id": conversation_id,
            "crag_action": crag.get("action", ""),
            "crag_score": crag.get("score"),
            "degraded": degraded,
            "timings": timings or {},
            "from_cache": from_cache,
        },
    )


def _faq_timings(
    t_start: float,
    retrieve_ms: int | None = None,
    generate_ms: int | None = None,
) -> dict[str, int]:
    """faq 阶段耗时快照（总耗时 + 可选检索/生成耗时，单位 ms）。"""
    timings: dict[str, int] = {"total_ms": round((time.monotonic() - t_start) * 1000)}
    if retrieve_ms is not None:
        timings["retrieve_ms"] = retrieve_ms
    if generate_ms is not None:
        timings["generate_ms"] = generate_ms
    return timings


async def _event_stream(
    req: ChatRequest,
    role: str = "user",
    access_token: str | None = None,
    username: str = "",
    user_id: str = "",
) -> AsyncIterator[str]:
    """SSE 事件生成器：意图路由 → 产品消歧（faq/task）→ 分流处理 → 流式返回。

    异常时发送 error 事件并结束流（不抛 500，因为流已开始无法改 status code）。
    username 透传给各意图处理器：done 路径持久化问答轮次时归属会话；
    user_id/username 透传消歧订单层（requester 身份）。

    T3 埋点（旁路）：t0=进入、t_first_delta=首个 delta 写出（TTFT）、t_done=流结束；
    阶段耗时经 handlers 回填 stage_ms；结束时统一落结构化日志 + 指标，异常被吞。
    """
    t0 = time.monotonic()
    t_first_delta: float | None = None
    intent = "unclear"
    stage_ms: dict[str, int] = {}
    metrics.inc_inflight()

    def _track(chunk: str) -> str:
        """记录首个 delta 的写出时刻（TTFT 定义），并原样透传 chunk。"""
        nonlocal t_first_delta
        if t_first_delta is None and chunk.startswith("event: delta"):
            t_first_delta = time.monotonic()
        return chunk

    try:
        # 1. 意图路由
        _t_route = time.monotonic()
        route_result = await route(req.query)
        stage_ms["intent_ms"] = round((time.monotonic() - _t_route) * 1000)
        intent = route_result.get("intent", "unclear")

        # 2. start 事件（conversation_id 供前端反馈关联本条问答；
        #    请求携带 conversation_id 时归属原会话，否则新建）
        conversation_id = req.conversation_id or str(uuid.uuid4())
        yield _sse_event(
            "start",
            {
                "query": req.query,
                "intent": intent,
                "conversation_id": conversation_id,
                "route_source": route_result.get("source", ""),
                # RBAC 角色：诊断面板确认检索权限分级实际生效的角色
                "role": role,
            },
        )

        history = [h.model_dump() for h in req.history] if req.history else None
        # 记忆窗口裁剪：统一由 DialogManager 处理（chat/faq/task 同语义）
        history = trim_history(history)

        # 人格语气指令（可选）：拼在既有 system 之后，只改语气不改职责；
        # 未知 persona 已在 persona_prompt 内 warning 并返回 None（回落默认）
        persona_suffix = persona_prompt(req.persona)

        # 情绪安全阀（规则层，T4）：命中时人格让位，共情优先指令注入在
        # persona 之前；faq/chat/task 三条链路统一应用
        override = emotion_override(req.query)
        if override:
            logger.info("[Personas] 情绪安全阀触发，人格让位")

        # 3. 产品消歧（phase15，仅 faq/task）：路由后、分发前执行。
        # pass（未提及别名/单候选）→ 原链路零改动；resolved → 静默定向（注记 query
        # + 确认指令 + task preset 实体）；clarify → 反问短路（不进 LLM/检索/工具）。
        disambiguation: dict | None = None
        if intent in ("faq", "task"):
            try:
                disambiguation = await disambiguate_product(
                    req.query,
                    history,
                    requester_user_id=user_id,
                    requester_username=username,
                )
            except Exception as e:  # 消歧任何异常不得阻塞主链路
                logger.warning("[Chat] 产品消歧失败（按直通处理）: %s", e)
                disambiguation = None
            if disambiguation and disambiguation.get("status") == "clarify":
                clarify_answer = disambiguation.get("clarify_text") or "请问您说的是哪一款产品？"
                candidates = _disambig_candidates_payload(disambiguation)
                await _persist_round_safe(
                    conversation_id, username, req.query, clarify_answer, intent=intent
                )
                yield _sse_event(
                    "disambiguation",
                    {"status": "clarify", "candidates": candidates},
                )
                yield _sse_event(
                    "done",
                    {
                        "answer": clarify_answer,
                        "conversation_id": conversation_id,
                        "disambiguation": {"status": "clarify", "candidates": candidates},
                    },
                )
                return
            if disambiguation and disambiguation.get("status") == "resolved":
                yield _sse_event(
                    "disambiguation",
                    {
                        "status": "resolved",
                        "method": disambiguation.get("method", ""),
                        "product_id": disambiguation.get("product_id"),
                        "product_name": disambiguation.get("product_name"),
                    },
                )

        # resolved 时以注记 query（原 query + 商品名）驱动缓存键/检索/Agent 决策，
        # 会话持久化仍用用户原话（original_query），不篡改会话历史
        eff_query = req.query
        confirm_suffix = _disambig_confirm_suffix(disambiguation)
        if disambiguation and disambiguation.get("status") == "resolved":
            eff_query = disambiguation.get("annotated_query") or req.query

        # 4. 按意图分流
        if intent == "faq":
            async for chunk in _handle_faq(
                eff_query,
                history,
                role=role,
                conversation_id=conversation_id,
                user_id=username,
                persona_suffix=persona_suffix,
                persona_id=req.persona or "",
                override=override,
                stage_ms=stage_ms,
                confirm_suffix=confirm_suffix,
                original_query=req.query,
            ):
                yield _track(chunk)
        elif intent == "task":
            # 消歧定向实体仅补缺：显式抽取结果优先（合并语义在 ToolAgent.run 内保证）
            preset_entities = None
            if (
                disambiguation
                and disambiguation.get("status") == "resolved"
                and disambiguation.get("product_id")
            ):
                preset_entities = {"product_id": disambiguation["product_id"]}
            async for chunk in _handle_task(
                eff_query,
                history,
                access_token=access_token,
                conversation_id=conversation_id,
                user_id=username,
                persona_suffix=persona_suffix,
                override=override,
                stage_ms=stage_ms,
                confirm_suffix=confirm_suffix,
                original_query=req.query,
                preset_entities=preset_entities,
            ):
                yield _track(chunk)
        elif intent == "chat":
            async for chunk in _handle_chat(
                req.query,
                history,
                persona_suffix=persona_suffix,
                conversation_id=conversation_id,
                user_id=username,
                override=override,
                stage_ms=stage_ms,
            ):
                yield _track(chunk)
        else:  # unclear
            await _persist_round_safe(
                conversation_id, username, req.query, _CLARIFY_ANSWER, intent="unclear"
            )
            yield _sse_event(
                "done",
                {
                    "answer": _CLARIFY_ANSWER,
                    "conversation_id": conversation_id,
                },
            )
    except Exception as e:
        logger.warning("[Chat] SSE 流处理异常: %s", e)
        yield _sse_event("error", {"message": str(e)})
    finally:
        # 旁路收口：无论正常结束 / 异常 / 客户端断开（GeneratorExit）都必须释放槽位并落点
        metrics.dec_inflight()
        _emit_perf(
            intent=intent,
            t0=t0,
            t_first_delta=t_first_delta,
            t_done=time.monotonic(),
            stage_ms=stage_ms,
        )


async def _handle_faq(
    query: str,
    history: list[dict[str, str]] | None = None,
    role: str = "user",
    conversation_id: str = "",
    user_id: str = "",
    persona_suffix: str | None = None,
    persona_id: str = "",
    override: str | None = None,
    stage_ms: dict[str, int] | None = None,
    confirm_suffix: str | None = None,
    original_query: str | None = None,
) -> AsyncIterator[str]:
    """faq 意图：retrieving → (rewriting) → generating → done。

    CRAG 决策与 engine.answer 共用同一门禁（should_rewrite_retry / retry_query_for）：
    - 空检索 / no_result → 直接 done(未找到)，不带空上下文生成（防空检索幻觉）
    - 低分 rewrite_retry → 用改写变体二次检索，命中时发 rewriting 事件；重检索仍空则复检
    - 整条链路包在 Langfuse 根 trace（chat_faq）里，done 回传 trace_id / conversation_id
      （bad case 归因时凭 trace_id 还原证据链，提交反馈时关联）
    - 每个 done 路径（含"未找到"）先持久化一轮问答再 yield（失败仅 warning）
    - 语义缓存按 role + persona 分桶：选人格不再放弃缓存，不同人格互不串桶；
      persona_id 传原始人格 id（空串落 default 桶），persona_suffix 只影响语气
    - stage_ms（T3，可选）：回填各阶段耗时供 SSE 收口统一落日志（旁路，不参与业务判断）
    - 消歧（phase15）：query 可能是注记 query（原 query + 商品名），缓存键/检索/生成
      共用之；original_query 传用户原话用于会话持久化（不篡改历史）；confirm_suffix
      为产品确认指令，追加在 override/persona 之后
    """
    _sm = stage_ms if stage_ms is not None else {}
    persist_query = original_query or query
    yield _sse_event("retrieving", {})
    # 阶段计时（后端精确）：检索耗时 / 生成耗时 / 全程，经 done.timings 回传诊断面板
    _t_start = time.monotonic()

    # 整条链路包在 Langfuse 根 trace（chat_faq）里——包含语义缓存命中分支：
    # 缓存命中同样回传 trace_id，确保任何 faq 结果都可按 trace 归因（bad case 回流用）。
    with _chat_trace_span("chat_faq", query) as span:
        # 语义缓存（L1/L2，按 role + persona 分桶隔离）：
        # - 同 query 同 role 不同 persona 各自独立桶，互不命中（语气不同答案不同）
        # - 命中直接 done，跳过整个检索+生成链路（高频问题秒回）
        # - 缓存故障（Redis 挂/异常）只降低命中率，不影响主流程
        try:
            cached = await semantic_cache.get(query, role=role, persona=persona_id)
        except Exception as e:
            logger.warning("[Chat] FAQ 缓存查询失败（忽略，直查）: %s", e)
            cached = None
        if cached:
            # 缓存命中指标（旁路）：level=L1/L2，按 role 维度（禁止跨角色命中已由分桶保证）
            _metric(metrics.inc_cache_hit, str(cached.get("from_cache", "")).lower() or "l2", role)
            await _persist_round_safe(
                conversation_id, user_id, persist_query, cached.get("answer", ""), intent="faq"
            )
            yield _faq_done_event(
                span,
                query,
                conversation_id,
                cached.get("answer", ""),
                cached.get("sources", []),
                {},
                [],
                timings=_faq_timings(_t_start),
                from_cache=cached.get("from_cache", ""),
                emotion_override_hit=override is not None,
            )
            return
        _metric(metrics.inc_cache_hit, "miss", role)

        retrieval = await rag_engine.retrieve(query, role=role)
        retrieve_ms = round((time.monotonic() - _t_start) * 1000)
        # 阶段耗时回填（T3）：rewrite/embed/recall/rerank/crag
        _merge_retrieval_stage_ms(_sm, retrieval)
        _sm["retrieve_ms"] = retrieve_ms

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
            _merge_retrieval_stage_ms(_sm, retry_retrieval)
            degraded = degraded + list(retry_retrieval.get("degraded", []))
            crag = retry_retrieval.get("crag", {})
            # P1 复检：重检索后仍空 / 仍 no_result → 走"未找到"，避免错误生成
            if not retry_retrieval.get("contexts") or crag.get("action") == "no_result":
                fallback = rag_engine.no_result_answer(query)
                await _persist_round_safe(
                    conversation_id, user_id, persist_query, fallback, intent="faq"
                )
                yield _faq_done_event(
                    span,
                    query,
                    conversation_id,
                    fallback,
                    [],
                    crag,
                    degraded,
                    timings=_faq_timings(_t_start, retrieve_ms),
                    emotion_override_hit=override is not None,
                )
                return
            contexts = retry_retrieval.get("contexts", [])
        elif crag.get("action") == "no_result":
            fallback = rag_engine.no_result_answer(query)
            await _persist_round_safe(conversation_id, user_id, persist_query, fallback, intent="faq")
            yield _faq_done_event(
                span,
                query,
                conversation_id,
                fallback,
                [],
                crag,
                degraded,
                timings=_faq_timings(_t_start, retrieve_ms),
                emotion_override_hit=override is not None,
            )
            return
        else:
            contexts = retrieval.get("contexts", [])

        yield _sse_event("generating", {})

        # 流式生成：逐 chunk 转发 delta（前端打字机），final 收口 done
        gen = {"answer": "", "sources": [], "degraded": False}
        _g0 = time.monotonic()
        async for chunk in rag_engine.generate_stream(
            query,
            contexts,
            history,
            # 注入顺序：既有 system（engine 内部）→ override → persona → 消歧确认（_compose_system）
            system_suffix=_compose_system(override, persona_suffix, confirm_suffix) or None,
        ):
            if "delta" in chunk:
                yield _sse_event("delta", {"delta": chunk["delta"]})
            else:
                gen = chunk["final"]
        generate_ms = round((time.monotonic() - _g0) * 1000)
        _sm["generate_ms"] = generate_ms
        _metric(metrics.observe_stage, "generate", generate_ms / 1000)
        # 生成阶段降级（LLM 模板兜底）并入追溯快照
        degraded = degraded + (["llm"] if gen.get("degraded") else [])

        # 语义缓存写入（非降级兜底才写；按 role + persona 分桶；缓存故障不影响主流程）
        if not gen.get("degraded"):
            try:
                await semantic_cache.set(
                    query,
                    gen.get("answer", ""),
                    gen.get("sources", []),
                    role=role,
                    persona=persona_id,
                )
            except Exception as e:
                logger.warning("[Chat] FAQ 缓存写入失败（忽略）: %s", e)

        await _persist_round_safe(
            conversation_id, user_id, persist_query, gen.get("answer", ""), intent="faq"
        )
        yield _faq_done_event(
            span,
            query,
            conversation_id,
            gen.get("answer", ""),
            gen.get("sources", []),
            crag,
            degraded,
            timings=_faq_timings(_t_start, retrieve_ms, generate_ms),
            emotion_override_hit=override is not None,
        )


async def _handle_task(
    query: str,
    history: list[dict[str, str]] | None = None,
    access_token: str | None = None,
    conversation_id: str = "",
    user_id: str = "",
    persona_suffix: str | None = None,
    override: str | None = None,
    stage_ms: dict[str, int] | None = None,
    confirm_suffix: str | None = None,
    original_query: str | None = None,
    preset_entities: dict[str, str] | None = None,
) -> AsyncIterator[str]:
    """task 意图：tool_call → tool_result → (多轮) → done。

    history 传入 ToolAgent：多轮场景（如「查订单 20260801001」→「物流到哪了？」）
    依赖上文订单号，ToolAgent 内部实体识别会从历史回溯补填工具参数。
    人格接入（T1）：persona_suffix / override 非空时构造
    DEFAULT_SYSTEM_PROMPT → override → persona_suffix → TASK_PERSONA_GUARD
    的 system prompt——人格只作用于 Final Answer 与 CLARIFY 的用户可见文本，
    Thought/Action/Action Input 决策协议不受影响；均空时保持默认构造（行为与
    无人格版本完全一致）。人格文本不注入 query 改写与工具参数。
    消歧（phase15）：query 可能是注记 query（驱动 Agent 决策与 search_knowledge）；
    preset_entities 为消歧定向实体（仅补缺）；confirm_suffix 为产品确认指令；
    original_query 传用户原话用于会话持久化。
    整条链路包在 Langfuse 根 trace（chat_task）里，done 回传 trace_id——
    task 结果同样可按 trace 归因（工具调用链还原/提交反馈关联）。
    done 路径先持久化一轮问答（answer 取 result["answer"]，失败仅 warning）。
    stage_ms（T3）：回填 Agent 全链路耗时与迭代轮数（旁路）。
    """
    _sm = stage_ms if stage_ms is not None else {}
    _t_agent = time.monotonic()
    with _chat_trace_span("chat_task", query) as span:
        client = MCPClient(access_token=access_token)
        if override or persona_suffix or confirm_suffix:
            agent = ToolAgent(
                system_prompt=_compose_system(
                    DEFAULT_SYSTEM_PROMPT,
                    override,
                    persona_suffix,
                    TASK_PERSONA_GUARD,
                    confirm_suffix,
                ),
                mcp_client=client,
            )
        else:
            agent = ToolAgent(mcp_client=client)
        if preset_entities:
            result = await agent.run(query, history=history, preset_entities=preset_entities)
        else:
            result = await agent.run(query, history=history)
        _sm["agent_ms"] = round((time.monotonic() - _t_agent) * 1000)
        _sm["iterations"] = int(result.get("iterations") or 0)

        tool_calls = result.get("tool_calls") or []
        for tc in tool_calls:
            tool_name = tc.get("name", "")
            arguments = tc.get("input") or {}
            yield _sse_event("tool_call", {"tool_name": tool_name, "arguments": arguments})
            yield _sse_event(
                "tool_result",
                {"tool_name": tool_name, "result": tc.get("result")},
            )

        answer = result.get("answer", "")
        trace_id = _span_trace_id(span)
        if span is not None:
            metadata = {
                "intent": "task",
                "conversation_id": conversation_id,
                "tools": [tc.get("name", "") for tc in tool_calls],
                "iterations": result.get("iterations"),
            }
            if override:
                metadata["emotion_override"] = True
            _safe_span_update(span, metadata=metadata)
        await _persist_round_safe(
            conversation_id,
            user_id,
            original_query or query,
            answer,
            intent="task",
        )
        yield _sse_event(
            "done",
            {
                "answer": answer,
                "trace_id": trace_id,
                "conversation_id": conversation_id,
            },
        )


async def _handle_chat(
    query: str,
    history: list[dict[str, str]] | None = None,
    persona_suffix: str | None = None,
    conversation_id: str = "",
    user_id: str = "",
    override: str | None = None,
    stage_ms: dict[str, int] | None = None,
) -> AsyncIterator[str]:
    """chat 意图：generating → done（历史拼入 prompt）。

    历史格式与 engine.generate 一致（用户/客服: 内容），按 MEMORY_WINDOW 已裁剪。
    注入顺序（_compose_system）：_CHAT_SYSTEM → override（情绪安全阀）→ persona。
    整条链路包在 Langfuse 根 trace（chat_chat）里，done 回传 trace_id。
    done 路径先持久化一轮问答（失败仅 warning，不阻塞响应）。
    stage_ms（T3）：回填生成阶段耗时（旁路）。
    """
    _sm = stage_ms if stage_ms is not None else {}
    with _chat_trace_span("chat_chat", query) as span:
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
        system = _compose_system(_CHAT_SYSTEM, override, persona_suffix)
        answer = ""
        _g0 = time.monotonic()
        try:
            async for delta in stream_llm(prompt, system=system, generation=True):
                answer += delta
                yield _sse_event("delta", {"delta": delta})
        except LLMUnavailableError:
            # 与 engine.generate 模板兜底保持一致的降级话术
            answer = "抱歉，服务暂时繁忙，请稍后重试或转人工客服。"
            _metric(metrics.inc_degradation, "llm", "unavailable")
        _gen_ms = round((time.monotonic() - _g0) * 1000)
        _sm["generate_ms"] = _gen_ms
        _metric(metrics.observe_stage, "generate", _gen_ms / 1000)
        trace_id = _span_trace_id(span)
        if span is not None:
            metadata = {"intent": "chat", "conversation_id": conversation_id}
            if override:
                metadata["emotion_override"] = True
            _safe_span_update(span, metadata=metadata)
        await _persist_round_safe(conversation_id, user_id, query, answer, intent="chat")
        yield _sse_event(
            "done",
            {
                "answer": answer,
                "trace_id": trace_id,
                "conversation_id": conversation_id,
            },
        )


@router.get("/personas")
async def chat_personas(user: Annotated[dict, Depends(get_current_user)]):
    """客服人格列表（前端语气选择器数据源，登录即可选）。"""
    return {"personas": list_personas()}


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
        _event_stream(
            req,
            role=user.get("role", "user"),
            access_token=user.get("access_token"),
            username=user.get("username", ""),
            user_id=user.get("user_id", ""),
        ),
        media_type=_SSE_MEDIA_TYPE,
    )
