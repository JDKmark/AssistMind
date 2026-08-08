"""对话上下文管理（DialogManager）。

集中「对话上下文」这一职责（此前散落三处：api/chat.py 的 history 裁剪、
agents/base.py 的 _extract_query、rag/engine.py 的历史拼接），统一入口 + 专项测试。

契约（供消费方使用，勿改形状）：
- trim_history(history) -> list | None：按 MEMORY_WINDOW 裁剪，None 原样返回
- extract_query(messages) -> str：取最后一条 HumanMessage 的 content
- format_history(history) -> str："用户: xxx\n客服: xxx" 拼接（engine 历史 prompt 格式）
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings

settings = get_settings()


def trim_history(
    history: list[dict[str, str]] | None,
) -> list[dict[str, str]] | None:
    """对话历史按记忆窗口裁剪（保留最近 MEMORY_WINDOW 条）。

    None 原样返回（调用方语义：无历史）；语义与 format_history 的窗口一致，
    防止超长历史撑爆 LLM 上下文。
    """
    if history is None:
        return None
    return history[-settings.MEMORY_WINDOW:]


def extract_query(messages: list[Any]) -> str:
    """从消息列表中提取用户当前问题（最后一条 HumanMessage）。

    单轮对话时仅有一条 HumanMessage；多轮对话（history 注入）时当前问题总是
    追加在最后，取最后一条而非第一条，保证检索与 LLM 提示中的「用户问题」
    是当前轮次的输入。
    """
    from langchain_core.messages import HumanMessage

    query = ""
    for m in messages:
        if isinstance(m, HumanMessage):
            query = m.content if hasattr(m, "content") else str(m)
    return query


def format_history(history: list[dict[str, str]] | None) -> str:
    """历史消息拼接为 LLM 可读文本（"用户: ...\n客服: ..."）。

    仅拼接最近 MEMORY_WINDOW 条；空/None 返回空串。格式与 engine.generate
    的历史 prompt 完全一致（消费方依赖该形状）。
    """
    if not history:
        return ""
    return "\n".join(
        f"{'用户' if h.get('role') == 'user' else '客服'}: {h.get('content', '')}"
        for h in history[-settings.MEMORY_WINDOW:]
    )
