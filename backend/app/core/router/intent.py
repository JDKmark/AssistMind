"""三级意图路由主入口。

路由顺序：规则 -> 语义 -> LLM，任一层命中即返回。
降级：embedding 失败或 LLM 不可用时返回 unclear 兜底。

intent_routes.json 通过 mtime 热加载（修改配置文件后下次调用即生效）。
"""

from __future__ import annotations

import json
import logging
import os

from app.core.infra.llm_factory import LLMUnavailableError, call_llm
from app.core.router import confidence, semantic

logger = logging.getLogger(__name__)

# intent_routes.json 位于 app/data/ 下（两层 .. 回到 app/）
_ROUTES_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "intent_routes.json"
)

_VALID_INTENTS = ("faq", "task", "chat", "unclear")

# 模块级缓存：按 json mtime 热加载
_routes_cache: dict = {"mtime": None, "data": None}


def _load_routes() -> dict:
    mtime = os.path.getmtime(_ROUTES_PATH)
    if _routes_cache["mtime"] != mtime or _routes_cache["data"] is None:
        with open(_ROUTES_PATH, encoding="utf-8") as f:
            _routes_cache["data"] = json.load(f)
        _routes_cache["mtime"] = mtime
    return _routes_cache["data"]


def _rule_match(query: str, routes: dict) -> str | None:
    """规则匹配：query 包含任一关键词即命中，按 json 顺序首个命中返回。"""
    for intent_name, cfg in routes.items():
        for kw in cfg.get("keywords", []):
            if kw and kw in query:
                return intent_name
    return None


_LLM_SYSTEM = (
    "你是意图分类器。将用户问题分为以下四类之一：\n"
    "- faq：产品文档/功能咨询（例如“XX 是什么”“如何配置”“区别”）\n"
    "- task：需要执行操作（例如创建工单、转人工、查询状态、申请开通）\n"
    "- chat：闲聊、问候、致谢、告别\n"
    "- unclear：意图不明或信息不足\n"
    "仅输出 JSON，不要输出其它内容，格式："
    '{"intent": "faq|task|chat|unclear", "confidence": 0.0到1.0的数字}'
)


def _build_llm_prompt(query: str) -> str:
    return f"用户问题：{query}\n请输出分类 JSON："


def _parse_llm_response(resp: str) -> dict:
    """解析 LLM 返回。优先 JSON，回退文本中查找意图名，置信度解析失败默认 0.7。"""
    raw = resp.strip() if isinstance(resp, str) else str(resp)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as e:
            logger.warning("[IntentRouter] LLM 响应 JSON 解析失败: %s, raw=%r", e, raw)
        else:
            intent = str(obj.get("intent", "")).strip().lower()
            if intent in _VALID_INTENTS:
                try:
                    conf = float(obj.get("confidence", 0.7))
                except (TypeError, ValueError):
                    conf = 0.7
                return {"intent": intent, "confidence": conf}
            logger.warning("[IntentRouter] LLM JSON 中 intent 非法: %r", raw)
    # 回退：在文本中查找意图名
    low = raw.lower()
    for intent in _VALID_INTENTS:
        if intent in low:
            return {"intent": intent, "confidence": 0.7}
    logger.warning("[IntentRouter] LLM 响应无法解析意图: %r", raw)
    return {"intent": "unclear", "confidence": 0.7}


async def _llm_classify(query: str) -> dict:
    resp = await call_llm(_build_llm_prompt(query), _LLM_SYSTEM)
    return _parse_llm_response(resp)


async def route(query: str) -> dict:
    """三级路由主入口。

    Returns:
        {"intent": str, "confidence": float, "source": str, "low_confidence": bool}
    """
    if not query or not query.strip():
        return {
            "intent": "unclear",
            "confidence": 0.0,
            "source": "fallback",
            "low_confidence": True,
        }

    routes = _load_routes()

    # 1. 规则层
    intent = _rule_match(query, routes)
    if intent is not None:
        return {
            "intent": intent,
            "confidence": 1.0,
            "source": "rule",
            "low_confidence": False,
        }

    # 2. 语义层
    try:
        sem = await semantic.semantic_route(query)
    except Exception as e:
        logger.warning("[IntentRouter] 语义路由异常: %s", e)
        sem = None
    if sem is not None:
        return sem

    # 3. LLM 层
    try:
        llm_result = await _llm_classify(query)
        conf = confidence.compute("llm", llm_result["confidence"])
        return {
            "intent": llm_result["intent"],
            "confidence": conf["confidence"],
            "source": "llm",
            "low_confidence": conf["low_confidence"],
        }
    except LLMUnavailableError as e:
        logger.warning("[IntentRouter] LLM 不可用，降级 unclear: %s", e)
    except Exception as e:
        logger.warning("[IntentRouter] LLM 分类异常，降级 unclear: %s", e)

    return {
        "intent": "unclear",
        "confidence": 0.0,
        "source": "fallback",
        "low_confidence": True,
    }
