"""多轮对话槽位状态机（轻量版）单元测试。

覆盖：
1. extract_slots：退款原因提取（关键词前缀 / 因为由于 / 原因词表 / 无原因）
2. extract_slots：订单号从多轮历史回溯（复用 entity_extractor）
3. extract_slots：工单 title / description 提取
4. required_slots：意图 → 必需槽位映射（未知意图返回空列表）
5. missing_slots：缺失判定（None / 空串 / 不在 dict 均视为缺失）

纯函数、无 session 存储，全部为同步测试。
"""

from __future__ import annotations

from app.core.dialog import REQUIRED_SLOTS, extract_slots, missing_slots, required_slots

# ---------- extract_slots：退款原因 ----------


def test_extract_slots_refund_reason():
    """「原因不想要了」+ 历史含「我要退货」「订单号是 20240801001」→ 订单号 + 原因。"""
    slots = extract_slots(
        "原因不想要了",
        history=[
            {"role": "user", "content": "我要退货"},
            {"role": "assistant", "content": "好的，请问您的订单号是多少？"},
            {"role": "user", "content": "订单号是 20240801001"},
        ],
    )
    assert slots["order_sn"] == "20240801001"
    assert slots["reason"] == "不想要了"


def test_extract_slots_reason_with_keyword():
    """「因为质量问题想退货」→ reason 含「质量问题」（因为后内容截断到意图动词前）。"""
    slots = extract_slots("因为质量问题想退货")
    assert "质量问题" in slots["reason"]


def test_extract_slots_reason_full():
    """「申请退款，原因：商品问题」→ reason 精确等于「商品问题」。"""
    slots = extract_slots("申请退款，原因：商品问题")
    assert slots["reason"] == "商品问题"


def test_extract_slots_reason_no_keyword():
    """「帮我退货」只表达意图、无原因 → reason 为 None。"""
    slots = extract_slots("帮我退货")
    assert slots.get("reason") is None


def test_extract_slots_reason_from_history():
    """query 无原因但历史 user 消息含原因 → 从历史回溯（多轮场景）。"""
    slots = extract_slots(
        "那就退了吧",
        history=[
            {"role": "user", "content": "我想退货"},
            {"role": "assistant", "content": "请问退款原因是什么呢？"},
            {"role": "user", "content": "尺寸不合适"},
        ],
    )
    assert slots["reason"] == "尺寸不合适"


# ---------- extract_slots：工单 title / description ----------


def test_extract_slots_title_description():
    """「标题是登录问题，描述是无法登录」→ title / description 分别提取。"""
    slots = extract_slots("创建工单，标题是登录问题，描述是无法登录")
    assert slots["title"] == "登录问题"
    assert slots["description"] == "无法登录"


# ---------- required_slots ----------


def test_required_slots_mapping():
    """refund / logistics / order / ticket 映射正确；未知意图返回空列表。"""
    assert required_slots("refund") == ["order_sn", "reason"]
    assert required_slots("logistics") == ["order_sn"]
    assert required_slots("order") == ["order_sn"]
    assert required_slots("ticket") == ["title", "description"]
    assert required_slots("unknown_intent") == []
    # REQUIRED_SLOTS 常量与 required_slots 一致
    assert REQUIRED_SLOTS["refund"] == ["order_sn", "reason"]


# ---------- missing_slots ----------


def test_missing_slots_refund():
    """退款缺 reason 时返回 ["reason"]；齐全时返回空列表。"""
    assert missing_slots("refund", {"order_sn": "x"}) == ["reason"]
    assert missing_slots("refund", {"order_sn": "x", "reason": "质量问题"}) == []


def test_missing_slots_none_values():
    """槽位值 None / 空串 / 不在 dict 中均视为缺失。"""
    assert missing_slots("refund", {"order_sn": None, "reason": ""}) == [
        "order_sn",
        "reason",
    ]
    assert missing_slots("refund", {"reason": "不想要了"}) == ["order_sn"]


def test_missing_slots_unknown_intent():
    """未知意图无必需槽位 → 空列表。"""
    assert missing_slots("unknown_intent", {}) == []
