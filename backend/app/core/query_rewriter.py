"""查询改写：Multi-Query + HyDE。

带可选 history（最近对话）：改写前先消解最后一条用户消息中的指代
（「它的价格」→「AssistMind 的价格」），变体仍是独立可检索的完整问句。
失败降级：LLM 失败时返回原问题（跳过改写），degraded=True。
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.core.dialog import format_history
from app.core.infra.llm_factory import LLMUnavailableError, call_llm

logger = logging.getLogger(__name__)
settings = get_settings()

_MULTI_QUERY_PROMPT = """你是查询改写助手。将用户问题改写为 {n} 个语义等价但表述不同的变体，用于提升检索召回率。

{history}要求：
- 每个变体独立成行，是可直接检索的完整问句
- 保持原意，只改变表述方式（同义词替换、句式变换、视角切换）
- 若用户问题含指代词（它/那个/这个/刚才提到的），先结合上文还原成具体对象再改写
- 覆盖不同的关键词组合，增加召回多样性
- 不要编造新信息，不要把上文内容写进变体

用户问题：{question}

请直接输出 {n} 个变体，每行一个，不要编号不要解释："""


_HYDE_PROMPT = """请根据用户问题，写一段假设性的答案（200字内）。这段答案用于检索相关文档，不需要完全正确，但要包含可能相关的关键词和概念。

{history}用户问题：{question}

假设答案："""


def _history_section(history: list[dict[str, str]] | None) -> str:
    """改写 prompt 的历史段（消解指代用）；无历史返回空串（模板原样）。"""
    if not history:
        return ""
    return (
        "对话上文（仅用于理解最后一条用户问题中的指代）：\n"
        + format_history(history)
        + "\n\n"
    )


async def rewrite(
    query: str, history: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """查询改写主入口。

    history（可选）：最近对话 [{role, content}]，仅用于在改写前消解指代；
    缺省 None 时行为与原版完全一致（向后兼容）。

    Returns:
        {
            "original": 原问题,
            "variants": [变体1, 变体2, ...],  # Multi-Query
            "hyde": "假设答案",  # HyDE（仅 strategy=hyde 或 auto 时）
            "all_queries": [原问题 + 变体],  # 用于 embedding 检索的所有 query
            "degraded": bool  # 是否降级
        }
    """
    result: dict[str, Any] = {
        "original": query,
        "variants": [],
        "hyde": None,
        "all_queries": [query],
        "degraded": False,
    }

    if not settings.QUERY_REWRITE_ENABLED:
        return result

    strategy = settings.QUERY_REWRITE_STRATEGY
    history_section = _history_section(history)

    # Multi-Query（默认）
    if strategy in ("multi_query", "auto"):
        try:
            prompt = _MULTI_QUERY_PROMPT.format(
                question=query,
                n=settings.QUERY_REWRITE_NUM_VARIANTS,
                history=history_section,
            )
            text = await call_llm(prompt, system="你是查询改写助手。", fast=True)
            variants = [v.strip() for v in text.strip().split("\n") if v.strip()]
            variants = [v for v in variants if len(v) > 3 and v != query][: settings.QUERY_REWRITE_NUM_VARIANTS]
            result["variants"] = variants
            result["all_queries"] = [query] + variants
        except LLMUnavailableError:
            logger.warning("[QueryRewriter] Multi-Query 降级：LLM 不可用，用原问题")
            result["degraded"] = True
        except Exception as e:
            logger.warning("[QueryRewriter] Multi-Query 失败: %s", e)
            result["degraded"] = True

    # HyDE（可选）
    if strategy in ("hyde", "auto"):
        try:
            text = await call_llm(
                _HYDE_PROMPT.format(question=query, history=history_section), fast=True
            )
            result["hyde"] = text.strip()
            if result["hyde"]:
                result["all_queries"].append(result["hyde"])
        except LLMUnavailableError:
            logger.warning("[QueryRewriter] HyDE 降级：LLM 不可用")
            result["degraded"] = True
        except Exception as e:
            logger.warning("[QueryRewriter] HyDE 失败: %s", e)
            result["degraded"] = True

    return result
