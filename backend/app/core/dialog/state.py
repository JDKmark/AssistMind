"""多轮对话槽位状态机（轻量版）：确定性槽位提取 + 意图必需槽位清单。

纯函数、无 session 存储、无状态：
- extract_slots(query, history)：从当前问题 + 历史确定性提取槽位
  （order_sn / product_id / reason / title / description），规则优先、零 LLM 成本
- required_slots(intent)：意图 → 必需槽位清单（未知意图返回空列表）
- missing_slots(intent, slots)：还缺哪些槽位（缺失 = None / 空串 / 不在 dict）

消费方：ToolAgent.think 在实体提示注入分支顺带注入「还缺槽位」提示，
给 LLM 确定性的追问依据（此前全靠 LLM 从 history 猜，无「还缺哪个槽位」判断）。
"""

from __future__ import annotations

import re

from app.core.mall.entity_extractor import extract

# 退款原因词表（用户直接说出原因词时兜底；不含「退货/退款」这类意图词）
_REASON_WORDS = (
    "七天无理由",
    "尺寸不合适",
    "重复购买",
    "质量问题",
    "商品问题",
    "发错货",
    "不想要了",
    "不想要",
)

# 原因前缀：「原因[:：]? 是? xxx」（如「原因不想要了」「原因：商品问题」「原因是质量问题」）
_REASON_PREFIX_RE = re.compile(r"原因[:：]?\s*是?\s*(.+?)(?=[，。；]|$)")
# 因果连词：「因为/由于 xxx」——截断在意图动词/标点前
# （如「因为质量问题想退货」→「质量问题」，「由于物流太慢，想退货」→「物流太慢」）
_REASON_CAUSE_RE = re.compile(r"(?:因为|由于)(?:是)?\s*(.+?)(?=想|要|所以|，|。|；|$)")
# 明确原因词：取最早出现的词（可带尾缀「了」，如「不想要了」）
_REASON_WORD_RE = re.compile("|".join(re.escape(w) + "了?" for w in _REASON_WORDS))
# 工单槽位：标题是/标题: xxx；描述是/描述: xxx；问题: xxx（兜底，要求冒号避免误匹配）
_TITLE_RE = re.compile(r"标题(?:是|[:：])\s*(.+?)(?=[，。；]|$)")
_DESCRIPTION_RE = re.compile(r"描述(?:是|[:：])\s*(.+?)(?=[，。；]|$)")
_DESCRIPTION_ALT_RE = re.compile(r"问题[:：]\s*(.+?)(?=[，。；]|$)")

# 意图 → 必需槽位清单
REQUIRED_SLOTS: dict[str, list[str]] = {
    "refund": ["order_sn", "reason"],  # 退货/退款
    "logistics": ["order_sn"],  # 查物流
    "order": ["order_sn"],  # 查订单
    "ticket": ["title", "description"],  # 创建工单
}


def required_slots(intent: str) -> list[str]:
    """意图 → 必需槽位清单（未知意图返回空列表）。"""
    return list(REQUIRED_SLOTS.get(intent, []))


def missing_slots(intent: str, slots: dict[str, str | None]) -> list[str]:
    """返回还缺哪些槽位（缺失 = 槽位值为 None / 空串 / 不在 dict 中）。"""
    return [slot for slot in required_slots(intent) if not slots.get(slot)]


def _clean_capture(text: str) -> str:
    """去掉捕获内容首尾的空白与标点（如「因为这个原因，想退货」去掉前导逗号）。"""
    return text.strip().lstrip("，,。；;：:、").rstrip("，,。；;：:、")


def _extract_reason(text: str) -> str | None:
    """从单个文本提取退款原因（三种模式依次尝试，返回第一个命中）。"""
    m = _REASON_PREFIX_RE.search(text)
    if m:
        return _clean_capture(m.group(1))
    m = _REASON_CAUSE_RE.search(text)
    if m:
        return _clean_capture(m.group(1))
    m = _REASON_WORD_RE.search(text)
    if m:
        return m.group(0)
    return None


def extract_slots(
    query: str, history: list[dict[str, str]] | None = None
) -> dict[str, str | None]:
    """从 query + history 确定性提取槽位（规则优先，不依赖 LLM）。

    Args:
        query: 用户当前输入
        history: 多轮对话历史 [{role, content}]（可选），
            用于「物流到哪了」等依赖上文订单号的多轮场景。

    Returns:
        {"order_sn", "product_id", "reason", "title", "description"}
        （未命中为 None）
    """
    entities = extract(query, history)
    slots: dict[str, str | None] = {
        "order_sn": entities.get("order_sn"),
        "product_id": entities.get("product_id"),
        "reason": None,
        "title": None,
        "description": None,
    }

    # reason：query 优先，历史 user 消息从近到远回溯
    user_history = [
        h.get("content", "")
        for h in (history or [])
        if isinstance(h, dict)
        and h.get("role") == "user"
        and isinstance(h.get("content", ""), str)
    ]
    for text in [query, *reversed(user_history)]:
        reason = _extract_reason(text)
        if reason:
            slots["reason"] = reason
            break

    # title / description：仅从当前 query 提取（工单场景）
    m = _TITLE_RE.search(query)
    if m:
        slots["title"] = _clean_capture(m.group(1))
    m = _DESCRIPTION_RE.search(query)
    if not m:
        m = _DESCRIPTION_ALT_RE.search(query)
    if m:
        slots["description"] = _clean_capture(m.group(1))

    return slots
