"""对话上下文管理（DialogManager）单元测试。

覆盖：
- trim_history：None 原样返回 / 超窗口裁剪 / 窗口内不动
- extract_query：单轮取唯一问题 / 多轮取最后一条 / 非 HumanMessage 忽略
- format_history：角色映射（用户/客服）/ 窗口裁剪 / 空与 None
- 与消费方契约一致（engine 历史 prompt 格式、MEMORY_WINDOW 语义）
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.dialog import extract_query, format_history, trim_history


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
    messages = [HumanMessage(content="查一下订单 20240801001")]
    assert extract_query(messages) == "查一下订单 20240801001"


def test_extract_query_multiturn_takes_last():
    """多轮：取最后一条 HumanMessage（当前轮输入）。"""
    messages = [
        HumanMessage(content="查一下订单 20240801001"),
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
