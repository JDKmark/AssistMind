"""工具调用 span 埋点（Langfuse）。

一次 Agent 请求在 Langfuse 里的完整证据链应当是：

    chat.ask（根 trace）
      ├── llm.call（意图判定 / 思考）
      ├── tool.query_order（执行工具）
      └── llm.call（生成答案）

`llm.call` 由 `llm_factory` 自动埋点；工具调用此前没有 span，导致 trace 里
「用户问了订单 → 回答里有订单状态」中间缺一环——无法判断答案用的是工具
返回的真实数据，还是模型凭上下文编造。本模块补上这一环。

约定与 `llm_factory` 保持一致：
- 未启用 Langfuse（`is_langfuse_enabled()` 为 False）时**零开销**：
  `new_tool_span` 直接返回 None，`finish_tool_span` 收到 None 立即返回。
- 埋点自身异常只记 warning，绝不影响工具执行与主链路。
- span 名统一为 `tool.<工具名>`；父 span 由 OTEL context 自动确定
  （外层存在 `chat.ask` 时自动挂为子 span，无需显式传 parent）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.core.infra.langfuse import get_langfuse, is_langfuse_enabled

logger = logging.getLogger(__name__)

# 单条 span 的输出上限：工具结果可能是整页订单列表，原样上报会撑大体积
_MAX_OUTPUT_CHARS = 2000


def _truncate(value: Any) -> Any:
    """结果过大时截断为字符串，保留原值类型以免污染小结果。"""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) <= _MAX_OUTPUT_CHARS:
        return value
    return text[:_MAX_OUTPUT_CHARS] + f"…（截断，原始长度 {len(text)}）"


def new_tool_span(name: str, arguments: dict[str, Any]) -> Any | None:
    """创建工具调用 span（未启用 Langfuse 时返回 None）。

    创建失败不抛出：返回 None，调用方按「不埋点」继续执行工具。
    """
    if not is_langfuse_enabled():
        return None
    client = get_langfuse()
    if client is None:
        return None
    try:
        return client.start_observation(
            name=f"tool.{name}",
            as_type="span",
            input=arguments,
            metadata={"tool": name},
        )
    except Exception as e:
        logger.warning("[Langfuse] 工具 span 创建失败（忽略，不影响工具执行）: %s", e)
        return None


def finish_tool_span(
    span: Any | None,
    started_at: float,
    *,
    result: Any = None,
    error: BaseException | None = None,
) -> None:
    """结束工具调用 span：写输出、耗时与成败（span 为 None 时立即返回）。

    失败时记 level=ERROR + status_message，便于在 Langfuse 里按错误级别筛选
    「工具调用失败」的 trace——这是文章 Case B 那类「底层失败但上层当成功」
    问题的排查入口。
    """
    if span is None:
        return
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    try:
        payload: dict[str, Any] = {
            "output": _truncate(result),
            "metadata": {"elapsed_ms": elapsed_ms, "ok": error is None},
        }
        if error is not None:
            payload["level"] = "ERROR"
            payload["status_message"] = str(error)[:2000]
        span.update(**payload)
        span.end()
    except Exception as e:
        logger.warning("[Langfuse] 工具 span 结束失败（忽略）: %s", e)
