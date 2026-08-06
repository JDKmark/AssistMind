"""CRAG 评估器（单层反思）。

评估检索结果与问题的相关性，决定：
- 相关性 >= 0.7：直接生成
- 0.3 <= 相关性 < 0.7：查询改写后重检索一次
- 相关性 < 0.3：返回"未找到"

失败降级：LLM 失败时默认走"相关"路径（score=1.0），不阻断主链路。

P1 修复：
- 正则表达式 r"([0-9]*\\.?[0-9]+)" 错误（不能匹配整数"1"）
- 修正为 r"(\\d+(?:\\.\\d+)?)"，正确匹配整数和小数
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.config import get_settings
from app.core.infra.llm_factory import LLMUnavailableError, call_llm

logger = logging.getLogger(__name__)
settings = get_settings()

# 修复：匹配整数或小数（原 r"([0-9]*\.?[0-9]+)" 不能匹配 "1"）
_SCORE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)")


_CRAG_PROMPT = """请评估以下检索结果对用户问题的相关性。

用户问题：{question}

检索结果：
{contexts}

请只输出一个 0 到 1 之间的数字，表示相关性得分：
- 1.0 = 完全相关，可直接生成答案
- 0.5 = 部分相关，可能需要补充检索
- 0.0 = 完全不相关

得分："""


async def evaluate(question: str, contexts: list[dict[str, Any]]) -> dict[str, Any]:
    """CRAG 评估。

    Returns:
        {
            "score": float,  # 0-1
            "action": "generate" | "rewrite_retry" | "no_result",
            "degraded": bool
        }
    """
    if not contexts:
        return {"score": 0.0, "action": "no_result", "degraded": False}

    try:
        ctx_text = "\n\n".join(
            [f"[{i+1}] {c.get('text', '')[:200]}" for i, c in enumerate(contexts[:5])]
        )
        prompt = _CRAG_PROMPT.format(question=question, contexts=ctx_text)
        result = await call_llm(prompt, system="你是相关性评估助手。")
        # 修复后的正则：能匹配 "0.8" "0.85" "1" "0" 等
        match = _SCORE_PATTERN.search(result)
        score = float(match.group(1)) if match else 1.0
        score = max(0.0, min(1.0, score))

        if score >= settings.CRAG_HIGH_THRESHOLD:
            action = "generate"
        elif score >= settings.CRAG_LOW_THRESHOLD:
            action = "rewrite_retry"
        else:
            action = "no_result"
        return {"score": score, "action": action, "degraded": False}
    except LLMUnavailableError:
        logger.warning("[CRAG] 降级：LLM 不可用，默认走 generate 路径")
        return {"score": 1.0, "action": "generate", "degraded": True}
    except Exception as e:
        logger.warning("[CRAG] 评估失败: %s", e)
        return {"score": 1.0, "action": "generate", "degraded": True}
