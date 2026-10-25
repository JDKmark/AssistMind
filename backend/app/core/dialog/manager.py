"""对话上下文管理（DialogManager）。

集中「对话上下文」这一职责（此前散落三处：api/chat.py 的 history 裁剪、
agents/base.py 的 _extract_query、rag/engine.py 的历史拼接），统一入口 + 专项测试。

契约（供消费方使用，勿改形状）：
- trim_history(history) -> list | None：按 MEMORY_WINDOW 裁剪，None 原样返回；
  头部摘要消息恒保留（不占窗口槽位）
- split_summary(history) -> (summary | None, rest)：摘要/普通消息分离，
  供消费方「先切窗口再重组 [summary, *rest[-N:]]」
- is_summary_message(item) -> bool：摘要消息判定（SUMMARY_MARK 前缀）
- extract_query(messages) -> str：取最后一条 HumanMessage 的 content
- format_history(history) -> str："用户: xxx\n客服: xxx" 拼接（engine 历史 prompt 格式；
  同样恒保留头部摘要）
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings

settings = get_settings()

# 摘要消息标记（store.load 注入在 history 头部的「此前对话摘要」assistant 消息）。
# 摘要不占窗口槽位：trim_history / format_history / intent 切片都必须恒保留，
# 否则下游一次 [-WINDOW:] 切片就把摘要裁掉（摘要静默失效）
SUMMARY_MARK = "（此前对话摘要："


def is_summary_message(item: Any) -> bool:
    """判断一条历史消息是否为服务端注入的摘要消息（content 前缀标记）。"""
    content = item.get("content", "") if isinstance(item, dict) else ""
    return isinstance(content, str) and content.startswith(SUMMARY_MARK)


def split_summary(
    history: list[dict[str, str]] | None,
) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
    """把头部摘要消息与普通历史分离。

    Returns:
        (summary_msg | None, rest)：摘要消息（若在头部）与其余普通消息。
        消费方先切窗口再重组：[summary, *rest[-N:]]，保证摘要恒在。
    """
    if history and is_summary_message(history[0]):
        return history[0], history[1:]
    return None, list(history or [])


def trim_history(
    history: list[dict[str, str]] | None,
) -> list[dict[str, str]] | None:
    """对话历史按记忆窗口裁剪（保留最近 MEMORY_WINDOW 条）。

    None 原样返回（调用方语义：无历史）；语义与 format_history 的窗口一致，
    防止超长历史撑爆 LLM 上下文。头部摘要消息恒保留，不占窗口槽位。
    """
    if history is None:
        return None
    summary, rest = split_summary(history)
    if summary:
        return [summary, *rest[-settings.MEMORY_WINDOW:]]
    return rest[-settings.MEMORY_WINDOW:]


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
    的历史 prompt 完全一致（消费方依赖该形状）。头部摘要消息恒保留，
    不占窗口槽位（否则 FAQ 生成/改写的一次窗口切片就把摘要裁掉）。
    """
    if not history:
        return ""
    summary, rest = split_summary(history)
    kept = ([summary, *rest[-settings.MEMORY_WINDOW:]] if summary else rest[-settings.MEMORY_WINDOW:])
    return "\n".join(
        f"{'用户' if h.get('role') == 'user' else '客服'}: {h.get('content', '')}"
        for h in kept
    )
