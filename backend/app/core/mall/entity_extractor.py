"""电商业务实体识别：从用户问题（+多轮历史）抽取订单号 / 商品 ID。

设计：规则层确定性优先（零 LLM 成本、无幻觉），LLM 兜底仅在规则未命中时可选启用。
建立「实体 → 工具参数」关系映射（ENTITY_TO_TOOLS），供 ToolAgent 参数补填：
LLM 决策调用工具但 input 缺关键参数时，用抽取结果补填，减少编造订单号与 CLARIFY 次数。

失败降级：LLM 兜底失败返回空 dict + logger.warning，不抛异常、不阻塞主链路。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 订单号：11 位纯数字、20 开头（匹配演示单号 20240801001，避免误匹配手机号 1xx…）。
# 用数字边界 (?<!\d)/(?!\d) 而非 \b——中文环境下 \b 不生效（中文非单词字符）
_ORDER_SN_RE = re.compile(r"(?<!\d)20\d{9}(?!\d)")
# 商品 ID：P + 3 位数字（P001-P005 及未来扩展）
_PRODUCT_ID_RE = re.compile(r"(?<!\w)P\d{3}(?!\w)")

# 实体 → 可用工具映射（ToolAgent.execute_tool 参数补填用）
ENTITY_TO_TOOLS: dict[str, list[str]] = {
    "order_sn": ["query_order", "query_logistics", "apply_refund"],
    "product_id": ["query_product"],
}


def _extract_from_text(text: str) -> dict[str, str]:
    """规则层抽取单个文本中的实体（确定性优先）。"""
    entities: dict[str, str] = {}
    m = _ORDER_SN_RE.search(text)
    if m:
        entities["order_sn"] = m.group(0)
    m = _PRODUCT_ID_RE.search(text)
    if m:
        entities["product_id"] = m.group(0)
    return entities


def extract(query: str, history: list[dict[str, str]] | None = None) -> dict[str, str]:
    """从当前问题（+多轮历史）抽取实体。

    Args:
        query: 用户当前输入
        history: 多轮对话历史 [{role, content}]（可选），用于「物流到哪了」等
            依赖上文订单号的多轮场景——当前问题未命中时从历史取最早提供的实体。

    Returns:
        {"order_sn": str | None, "product_id": str | None}（未命中为 None）
    """
    entities = _extract_from_text(query)

    # 当前问题未命中 → 从历史中回溯（多轮上下文，如「订单号是 20240801001」「物流到哪了？」）
    if not entities and history:
        for msg in reversed(history):
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if not isinstance(content, str):
                continue
            hist_entities = _extract_from_text(content)
            if hist_entities:
                entities.update(hist_entities)
                logger.info("[MallEntity] 从历史消息提取实体: %s", hist_entities)
                break

    return {
        "order_sn": entities.get("order_sn"),
        "product_id": entities.get("product_id"),
    }


def fill_tool_args(
    tool_name: str, arguments: dict[str, Any], entities: dict[str, str]
) -> tuple[dict[str, Any], bool]:
    """按「实体 → 工具」映射补填缺失的工具参数。

    Args:
        tool_name: 工具名
        arguments: LLM 给出的工具参数（可能缺 order_sn / product_id）
        entities: extract() 的抽取结果

    Returns:
        (补填后的 arguments, 是否发生了补填)
    """
    filled = dict(arguments)
    filled_flag = False
    for entity_name, tools in ENTITY_TO_TOOLS.items():
        if tool_name in tools and not filled.get(entity_name) and entities.get(entity_name):
            filled[entity_name] = entities[entity_name]
            filled_flag = True
    return filled, filled_flag
