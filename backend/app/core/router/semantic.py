"""语义路由：基于 embedding 余弦相似度的意图匹配。

样本 embedding 在首次调用时构建并缓存，按 intent_routes.json 的 mtime 失效重建。
命中条件：最高相似度 >= INTENT_SEMANTIC_THRESHOLD 且
         (最高 - 次高) >= INTENT_SEMANTIC_MARGIN。
未命中返回 None，由上层进入 LLM 兜底。
"""

from __future__ import annotations

import json
import logging
import math
import os

from app.config import get_settings
from app.core.rag.embedding import embed_async, embed_one

logger = logging.getLogger(__name__)
settings = get_settings()

# intent_routes.json 位于 app/data/ 下（两层 .. 回到 app/）
_ROUTES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "intent_routes.json"
)

# 模块级缓存：按 json mtime 失效重建
_sample_cache: dict = {"mtime": None, "samples": [], "embeddings": []}


def _load_routes() -> dict:
    with open(_ROUTES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _collect_samples(routes: dict) -> list[tuple[str, str]]:
    """按 json 顺序收集 (intent, sample_text)。"""
    samples: list[tuple[str, str]] = []
    for intent_name, cfg in routes.items():
        for s in cfg.get("samples", []):
            samples.append((intent_name, s))
    return samples


async def _ensure_cache() -> None:
    """构建/刷新样本 embedding 缓存。失败不缓存，下次调用重试。"""
    mtime = os.path.getmtime(_ROUTES_PATH)
    if _sample_cache["mtime"] == mtime and _sample_cache["embeddings"]:
        return
    routes = _load_routes()
    samples = _collect_samples(routes)
    texts = [s for _, s in samples]
    if not texts:
        _sample_cache["mtime"] = mtime
        _sample_cache["samples"] = []
        _sample_cache["embeddings"] = []
        return
    embs = await embed_async(texts)
    if embs is None:
        logger.warning("[SemanticRouter] 样本 embedding 失败，本次跳过语义路由")
        # 不缓存失败，下次调用重试
        _sample_cache["mtime"] = None
        _sample_cache["samples"] = []
        _sample_cache["embeddings"] = []
        return
    _sample_cache["mtime"] = mtime
    _sample_cache["samples"] = samples
    _sample_cache["embeddings"] = embs


def _cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    for x, y in zip(a, b):
        dot += x * y
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def semantic_route(query: str) -> dict | None:
    """语义路由。命中返回意图字典，未命中返回 None。"""
    q_emb = await embed_one(query)
    if q_emb is None:
        return None
    await _ensure_cache()
    embeddings = _sample_cache["embeddings"]
    samples = _sample_cache["samples"]
    if not embeddings or not samples:
        return None

    sims = [_cosine(q_emb, emb) for emb in embeddings]
    sorted_sims = sorted(sims, reverse=True)
    best = sorted_sims[0]
    second = sorted_sims[1] if len(sorted_sims) > 1 else 0.0

    threshold = settings.INTENT_SEMANTIC_THRESHOLD
    margin = settings.INTENT_SEMANTIC_MARGIN
    if best >= threshold and (best - second) >= margin:
        best_idx = sims.index(best)
        intent = samples[best_idx][0]
        return {
            "intent": intent,
            "confidence": best,
            "source": "semantic",
            "low_confidence": False,
        }
    return None


def reset_cache() -> None:
    """重置样本缓存（测试/运维用）。"""
    _sample_cache["mtime"] = None
    _sample_cache["samples"] = []
    _sample_cache["embeddings"] = []
