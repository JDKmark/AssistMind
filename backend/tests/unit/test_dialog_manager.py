"""对话上下文管理（DialogManager）单元测试。

覆盖：
- trim_history：None 原样返回 / 超窗口裁剪 / 窗口内不动
- extract_query：单轮取唯一问题 / 多轮取最后一条 / 非 HumanMessage 忽略
- format_history：角色映射（用户/客服）/ 窗口裁剪 / 空与 None
- 与消费方契约一致（engine 历史 prompt 格式、MEMORY_WINDOW 语义）
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.config import get_settings
from app.core.dialog import extract_query, format_history, trim_history

settings = get_settings()


def test_trim_history_none_passthrough():
    """None 原样返回（无历史语义不变）。"""
    assert trim_history(None) is None


def test_trim_history_within_window_unchanged():
    """窗口内历史不动。"""
    history = [{"role": "user", "content": f"第{i}条"} for i in range(3)]
    assert trim_history(history) == history


def test_trim_history_truncates_to_window(monkeypatch):
    """超过 MEMORY_WINDOW 只保留最近 N 条。"""
    from app.core.dialog import manager as dm

    monkeypatch.setattr(dm.settings, "MEMORY_WINDOW", 2)
    history = [{"role": "user", "content": f"第{i}条"} for i in range(5)]
    result = trim_history(history)
    assert len(result) == 2
    assert result[0]["content"] == "第3条"
    assert result[1]["content"] == "第4条"


def test_extract_query_single_turn():
    """单轮：唯一 HumanMessage 即当前问题。"""
    messages = [HumanMessage(content="查一下订单 20260801001")]
    assert extract_query(messages) == "查一下订单 20260801001"


def test_extract_query_multiturn_takes_last():
    """多轮：取最后一条 HumanMessage（当前轮输入）。"""
    messages = [
        HumanMessage(content="查一下订单 20260801001"),
        AIMessage(content="您的订单已发货。"),
        HumanMessage(content="物流到哪了？"),
    ]
    assert extract_query(messages) == "物流到哪了？"


def test_extract_query_ignores_non_human():
    """SystemMessage / AIMessage 不影响提取。"""
    messages = [SystemMessage(content="系统提示"), AIMessage(content="你好")]
    assert extract_query(messages) == ""


def test_format_history_role_mapping():
    """用户/客服角色映射格式。"""
    history = [
        {"role": "user", "content": "查一下订单"},
        {"role": "assistant", "content": "您的订单已发货"},
    ]
    assert format_history(history) == "用户: 查一下订单\n客服: 您的订单已发货"


def test_format_history_empty_and_none():
    """空列表与 None 返回空串。"""
    assert format_history([]) == ""
    assert format_history(None) == ""


def test_format_history_truncates_to_window(monkeypatch):
    """超过 MEMORY_WINDOW 只拼接最近 N 条。"""
    from app.core.dialog import manager as dm

    monkeypatch.setattr(dm.settings, "MEMORY_WINDOW", 1)
    history = [
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": "客服一"},
    ]
    assert format_history(history) == "客服: 客服一"


# ---------- 摘要消息不占窗口槽位（跨下游不变量）----------
# 背景：store.load 注入的「此前对话摘要」在头部；下游 format_history /
# trim_history 的一次 [-WINDOW:] 切片曾把摘要裁掉（摘要静默失效）——
# 摘要恒保留是三意图一致的硬约束。


_SUMMARY_MSG = {"role": "assistant", "content": "（此前对话摘要：用户咨询过退款政策）"}


def _full_history() -> list[dict[str, str]]:
    """摘要 + 12 条普通消息（超过窗口）。"""
    msgs = [_SUMMARY_MSG]
    for i in range(12):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"消息{i}"})
    return msgs


def test_trim_history_keeps_summary_outside_window():
    trimmed = trim_history(_full_history())
    assert trimmed[0] == _SUMMARY_MSG
    assert len(trimmed) == settings.MEMORY_WINDOW + 1  # 摘要 + 窗口内普通消息
    assert trimmed[1]["content"] == "消息2"  # 普通消息仍裁到最近窗口


def test_format_history_keeps_summary_outside_window():
    text = format_history(_full_history())
    assert "此前对话摘要" in text
    # 普通消息同样只保留最近窗口
    assert "消息0" not in text
    assert "消息11" in text


def test_trim_and_format_unchanged_without_summary():
    """无摘要时行为与原版完全一致（向后兼容）。"""
    history = [{"role": "user", "content": f"m{i}"} for i in range(5)]
    assert trim_history(history) == history
    assert format_history(history) == "\n".join(f"用户: m{i}" for i in range(5))


def test_engine_generate_prompt_keeps_summary():
    """FAQ 生成 prompt 保留摘要（A1 回归钉：曾因窗口切片静默失效）。"""
    from app.core.rag.engine import _build_generate_prompt

    prompt = _build_generate_prompt("现在的问题", [{"text": "ctx"}], _full_history())
    assert "此前对话摘要" in prompt
    assert "现在的问题" in prompt
