"""意图置信度封装。

根据命中来源计算 confidence 与 low_confidence：
- 规则命中：confidence=1.0，必定高置信
- 语义命中：confidence=相似度得分，低于阈值则低置信
- LLM 命中：confidence=LLM 返回置信度（解析失败默认 0.7），低于阈值则低置信
"""

from __future__ import annotations


def compute(source: str, raw_score: float, threshold: float = 0.6) -> dict:
    """计算置信度与低置信标记。

    Args:
        source: 命中来源（rule / semantic / llm）
        raw_score: 原始得分（语义为相似度，LLM 为返回置信度）
        threshold: 低置信阈值，低于该值标记 low_confidence=True

    Returns:
        {"confidence": float, "low_confidence": bool}
    """
    if source == "rule":
        return {"confidence": 1.0, "low_confidence": False}
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 0.0
    return {"confidence": score, "low_confidence": score < threshold}
