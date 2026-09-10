"""电商业务实体识别：从用户问题（+多轮历史）抽取订单号 / 商品 ID。

设计：规则层确定性优先（零 LLM 成本、无幻觉），LLM 兜底仅在规则未命中时可选启用
（配置 ENTITY_LLM_FALLBACK=True，默认关闭——规则层在评估中已 9/9 全过，兜底只作
长尾口语化表达的补充）。
建立「实体 → 工具参数」关系映射（ENTITY_TO_TOOLS），供 ToolAgent 参数补填：
LLM 决策调用工具但 input 缺关键参数时，用抽取结果补填，减少编造订单号与 CLARIFY 次数。

失败降级：LLM 兜底失败（provider 不可用 / 超时 / 非 JSON / 字段非法）→ 返回空 dict +
logger.warning（degraded 语义），不抛异常、不阻塞主链路（同步规则层 extract 则零 LLM 成本、
永不失败）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.config import get_settings
from app.core.infra.llm_factory import LLMUnavailableError, call_llm

logger = logging.getLogger(__name__)
settings = get_settings()

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


# ---- LLM 兜底（可选启用，仅规则未命中时触发）----

_LLM_SYSTEM_PROMPT = "你是电商订单实体抽取助手。只输出 JSON，不要输出任何其他内容。"
_LLM_EXTRACT_PROMPT = """从用户消息中提取业务实体，规则如下：
- order_sn：11 位数字、以 20 开头的订单号（如 20240801001）；手机号（1 开头）不是订单号，不要提取
- product_id：大写 P + 3 位数字的商品 ID（如 P001）
- 没有找到对应实体时填 null

输出严格 JSON（不要 markdown 代码块）：
{"order_sn": "..." 或 null, "product_id": "..." 或 null}

用户消息：
{query}"""


def _parse_llm_entities(raw: str) -> dict[str, str | None]:
    """解析 LLM 兜底的 JSON 输出，并用规则层正则全量校验（防幻觉/手机号误报）。

    校验失败的字段按未提取处理（返回 None），不把非法值放进补填链路。
    """
    text = raw.strip()
    # 兼容 LLM 偶发的 markdown 代码块包裹
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # 二次尝试：截取首个 { ... } 片段（LLM 可能夹带解释文字）
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return {"order_sn": None, "product_id": None}
        try:
            data = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return {"order_sn": None, "product_id": None}

    if not isinstance(data, dict):
        return {"order_sn": None, "product_id": None}

    order_sn = data.get("order_sn")
    product_id = data.get("product_id")
    entities: dict[str, str | None] = {"order_sn": None, "product_id": None}
    if isinstance(order_sn, str) and _ORDER_SN_RE.fullmatch(order_sn):
        entities["order_sn"] = order_sn
    if isinstance(product_id, str) and _PRODUCT_ID_RE.fullmatch(product_id):
        entities["product_id"] = product_id
    return entities


async def extract_with_llm(
    query: str, history: list[dict[str, str]] | None = None
) -> dict[str, str | None]:
    """抽取实体：规则层优先；未命中且 ENTITY_LLM_FALLBACK 开启时走一次 LLM 兜底。

    - 规则命中：零 LLM 成本，与 extract() 行为完全一致
    - 兜底失败（provider 不可用 / 非 JSON / 字段非法）：返回空 dict + logger.warning，
      degraded 语义，绝不抛异常、不阻塞 Agent 主链路
    """
    entities = extract(query, history)
    if entities["order_sn"] or entities["product_id"]:
        return entities

    if not settings.ENTITY_LLM_FALLBACK:
        return entities

    try:
        # 用 replace 而非 str.format：模板内含 JSON 示例花括号（{order_sn} 等），
        # format 会把它们当占位符解析导致 KeyError
        raw = await call_llm(
            _LLM_EXTRACT_PROMPT.replace("{query}", query), system=_LLM_SYSTEM_PROMPT
        )
    except LLMUnavailableError as e:
        logger.warning("[MallEntity] LLM 兜底不可用（degraded，返回空实体）: %s", e)
        return entities
    except Exception as e:  # 兜底路径任何异常都不能阻塞主链路
        logger.warning("[MallEntity] LLM 兜底异常（degraded，返回空实体）: %s", e)
        return entities

    llm_entities = _parse_llm_entities(raw)
    if llm_entities["order_sn"] or llm_entities["product_id"]:
        logger.info("[MallEntity] LLM 兜底提取实体: %s", llm_entities)
        return llm_entities
    logger.info("[MallEntity] LLM 兜底未提取到实体（保持规则层结果）")
    return entities


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
