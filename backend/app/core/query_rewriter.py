"""查询改写：Multi-Query + HyDE。

失败降级：LLM 失败时返回原问题（跳过改写），degraded=True。
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.core.infra.llm_factory import LLMUnavailableError, call_llm

logger = logging.getLogger(__name__)
settings = get_settings()

_MULTI_QUERY_PROMPT = """你是查询改写助手。将用户问题改写为 {n} 个语义等价但表述不同的变体，用于提升检索召回率。

要求：
- 每个变体独立成行
- 保持原意，只改变表述方式（同义词替换、句式变换、视角切换）
- 覆盖不同的关键词组合，增加召回多样性
- 不要编造新信息

用户问题：{question}

请直接输出 {n} 个变体，每行一个，不要编号不要解释："""


_HYDE_PROMPT = """请根据用户问题，写一段假设性的答案（200字内）。这段答案用于检索相关文档，不需要完全正确，但要包含可能相关的关键词和概念。

用户问题：{question}

假设答案："""


async def rewrite(query: str) -> dict[str, Any]:
    """查询改写主入口。

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

    # Multi-Query（默认）
    if strategy in ("multi_query", "auto"):
        try:
            prompt = _MULTI_QUERY_PROMPT.format(
                question=query, n=settings.QUERY_REWRITE_NUM_VARIANTS
            )
            text = await call_llm(prompt, system="你是查询改写助手。")
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
            text = await call_llm(_HYDE_PROMPT.format(question=query))
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
