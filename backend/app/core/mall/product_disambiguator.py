"""产品消歧管道：同型号跨品类咨询先定向，再进入检索/工具链路。

场景：用户只说「我的 S1 Pro 不吸了」——系统需先分清是吸奶器（P006）还是
扫地机器人（P007），而不是拿歧义 query 直接检索导致答非所问。

管道（层序不可调换，见 .trae/specs/phase15-product-disambiguation/spec.md 第 2 章）：
  ① 别名召回（query + 最近 4 条历史，归一化子串匹配）
     ├─ 0 命中 / 1 候选 → status=pass（原链路零改动）
  ② 信号打分（symptom_keywords 在当前 query 的命中数；唯一最高分 >0 → signal）
  ③ 订单交集（requester 的 my_orders 商品 ∩ 候选；唯一交集 → orders）
  ④ LLM 兜底（仅 DISAMBIG_LLM_FALLBACK=True，默认关；fast=True 快速失败）
  ⑤ 反问（clarify，携带候选 name+spec，由编排层短路 LLM/检索/工具）

层序约束：信号先于订单——query 中的显式新鲜信号（如点名「扫地机器人」）优先于
订单历史（用户可能替他人咨询或设备未入订单），否则「反问后回复定向」会错定向到
订单持有款。闲聊污染由意图门控兜底：消歧仅由编排层在 faq/task 意图触发。

降级路径（不可静默）：候选详情查询失败剔除候选；my_orders 失败/身份为空跳过订单层；
配置缺失/非法沿用空配置或旧配置；LLM 兜底失败回落反问——均 logger.warning。
规则层零 LLM；LLM 兜底输出 MUST 经候选集全量校验（防幻觉，同构
entity_extractor._parse_llm_entities 的解析+校验写法）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from typing import Any

from app.config import get_settings
from app.core.infra.llm_factory import LLMUnavailableError, call_llm
from app.core.mall import data_source as mall_ds

logger = logging.getLogger(__name__)
settings = get_settings()

# app/core/mall/product_disambiguator.py → app/data/product_models.json
_MODELS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "product_models.json")

# 模块级缓存：按 json mtime 热加载（与 personas.json / intent_routes.json 同模式）
_models_cache: dict = {"mtime": None, "data": None}

# 历史别名扫描窗口：最近 4 条消息（任意角色）
_HISTORY_WINDOW = 4

# 配置校验：product_id 形如 P001（与 entity_extractor 的商品 ID 规则一致）
_PRODUCT_ID_RE = re.compile(r"P\d{3}")

# 候选详情查询用的角色：消歧内部仅消费 id/name/spec（对外候选载荷只含这三项），
# 按 user 最小披露视角查询，与 RBAC 展示策略不冲突
_INTERNAL_QUERY_ROLE = "user"


def _validate_models(raw: Any) -> list[dict] | None:
    """校验别名配置结构，合法返回归一化后的 aliases 列表，非法返回 None。

    校验规则（整体校验，任一条目非法即整份配置拒绝——沿用最近一次有效配置）：
    顶层为 dict 且含 aliases 列表；alias 非空字符串；products 非空列表；
    product_id 匹配 P\\d{3}；symptom_keywords 为字符串列表。
    """
    if not isinstance(raw, dict):
        return None
    aliases = raw.get("aliases")
    if not isinstance(aliases, list):
        return None
    validated: list[dict] = []
    for entry in aliases:
        if not isinstance(entry, dict):
            return None
        alias = entry.get("alias")
        products = entry.get("products")
        if not isinstance(alias, str) or not alias.strip():
            return None
        if not isinstance(products, list) or not products:
            return None
        validated_products: list[dict] = []
        for product in products:
            if not isinstance(product, dict):
                return None
            product_id = product.get("product_id")
            keywords = product.get("symptom_keywords")
            if not isinstance(product_id, str) or not _PRODUCT_ID_RE.fullmatch(product_id):
                return None
            if not isinstance(keywords, list) or not all(
                isinstance(kw, str) for kw in keywords
            ):
                return None
            validated_products.append(
                {"product_id": product_id, "symptom_keywords": list(keywords)}
            )
        validated.append({"alias": alias, "products": validated_products})
    return validated


def _load_models() -> list[dict]:
    """加载别名配置（mtime 热加载；缺失/非法沿用旧配置，首装无旧配置视为空）。"""
    try:
        mtime = os.path.getmtime(_MODELS_PATH)
    except OSError:
        # 配置文件不存在（如部署环境未带该文件）→ 空配置，所有请求 pass
        logger.warning("[Disambig] 配置文件不可读: %s", _MODELS_PATH)
        return _models_cache.get("data") or []
    if _models_cache["mtime"] == mtime and _models_cache["data"] is not None:
        return _models_cache["data"]
    try:
        with open(_MODELS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("[Disambig] 配置解析失败（沿用旧配置）: %s", e)
        return _models_cache.get("data") or []
    validated = _validate_models(raw)
    if validated is None:
        logger.warning("[Disambig] 配置字段校验失败（沿用旧配置）: %s", _MODELS_PATH)
        return _models_cache.get("data") or []
    _models_cache["data"] = validated
    _models_cache["mtime"] = mtime
    return validated


def _normalize(text: str) -> str:
    """归一化：casefold + 全角转半角（NFKC）+ 空白折叠为单空格。"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _nospace(text: str) -> str:
    """去空格文本（覆盖「s1pro」等无空格写法的匹配）。"""
    return re.sub(r"\s+", "", text)


def _history_texts(history: list[dict[str, str]] | None) -> list[str]:
    """最近 N 条历史消息文本（任意角色，超出窗口的不参与召回）。"""
    if not history:
        return []
    texts = []
    for msg in history[-_HISTORY_WINDOW:]:
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, str) and content:
            texts.append(content)
    return texts


def _recall_alias(
    query: str, history: list[dict[str, str]] | None, aliases: list[dict]
) -> tuple[dict, str] | None:
    """别名召回：query + 最近 4 条历史做归一化子串匹配（含去空格兜底）。

    返回 (命中的别名条目, alias)；无命中返回 None。多别名同时命中取配置序第一个
    （外置配置按业务优先级排序）。
    """
    q_norm = _normalize(query)
    texts = [q_norm] + [_normalize(t) for t in _history_texts(history)]
    texts_nospace = [_nospace(t) for t in texts]
    for entry in aliases:
        alias_norm = _normalize(entry["alias"])
        alias_nospace = _nospace(alias_norm)
        if not alias_norm:
            continue
        for text, text_nospace in zip(texts, texts_nospace):
            if alias_norm in text or (alias_nospace and alias_nospace in text_nospace):
                return entry, entry["alias"]
    return None


def _signal_scores(candidates: list[dict], query_norm: str) -> list[int]:
    """信号打分：各候选 symptom_keywords 在归一化 query 中的命中词数。"""
    query_nospace = _nospace(query_norm)
    scores = []
    for candidate in candidates:
        score = 0
        for keyword in candidate.get("symptom_keywords") or []:
            kw_norm = _normalize(keyword)
            if kw_norm and (kw_norm in query_norm or _nospace(kw_norm) in query_nospace):
                score += 1
        scores.append(score)
    return scores


async def _fetch_candidates(raw_candidates: list[dict]) -> list[dict]:
    """取候选商品详情（name/spec）；查询失败/不存在的候选剔除并 warning。"""
    enriched: list[dict] = []
    for candidate in raw_candidates:
        product_id = candidate["product_id"]
        try:
            detail = await mall_ds.query_product(product_id, requester_role=_INTERNAL_QUERY_ROLE)
        except Exception as e:
            logger.warning("[Disambig] 候选 %s 商品详情查询失败（剔除）: %s", product_id, e)
            continue
        if not detail:
            logger.warning("[Disambig] 候选 %s 商品不存在（剔除）", product_id)
            continue
        enriched.append(
            {
                "product_id": product_id,
                "symptom_keywords": list(candidate.get("symptom_keywords") or []),
                "name": detail.get("name", ""),
                "spec": detail.get("spec", ""),
            }
        )
    return enriched


async def _order_product_ids(requester_user_id: str, requester_username: str) -> set[str] | None:
    """requester 订单中的商品 id 集合；身份为空/查询失败返回 None（跳过订单层）。"""
    if not requester_user_id and not requester_username:
        logger.warning("[Disambig] 请求者身份为空，跳过订单层")
        return None
    try:
        result = await mall_ds.my_orders(
            requester_user_id=requester_user_id,
            requester_username=requester_username,
        )
    except Exception as e:
        logger.warning("[Disambig] my_orders 查询失败，跳过订单层: %s", e)
        return None
    ids: set[str] = set()
    for order in result.get("orders") or []:
        for item in order.get("items") or []:
            product_id = item.get("product_id")
            if product_id:
                ids.add(product_id)
    return ids


def _candidates_payload(
    enriched: list[dict], scores: list[int], order_ids: set[str] | None
) -> list[dict]:
    """对外候选载荷：{product_id, name, spec, score, in_orders}。"""
    payload = []
    for candidate, score in zip(enriched, scores):
        payload.append(
            {
                "product_id": candidate["product_id"],
                "name": candidate["name"],
                "spec": candidate["spec"],
                "score": score,
                "in_orders": bool(order_ids and candidate["product_id"] in order_ids),
            }
        )
    return payload


def _result(
    status: str,
    method: str,
    query: str,
    product_id: str | None = None,
    product_name: str | None = None,
    candidates: list[dict] | None = None,
    clarify_text: str = "",
) -> dict:
    """统一返回 dict 契约（字段见 spec 第 5 章「产品消歧管道」，不得增删）。"""
    return {
        "status": status,
        "method": method,
        "product_id": product_id,
        "product_name": product_name,
        # resolved 时注记商品名（faq 缓存键/检索、task Agent 决策共用）；
        # pass/clarify 保持原 query
        "annotated_query": f"{query} {product_name}" if product_name else query,
        "confirm_hint": status == "resolved",
        "candidates": candidates or [],
        "clarify_text": clarify_text,
    }


def _clarify_text(alias: str, candidates: list[dict]) -> str:
    """反问文案模板：渲染候选区分性属性（名称 + spec）。"""
    joined = "和".join(f"{c['name']}（{c['spec']}）" for c in candidates)
    count = "两款" if len(candidates) == 2 else f"{len(candidates)} 款"
    return f"{alias} 有{count}产品：{joined}。请问您说的是哪一款？"


# ---- LLM 兜底（可选启用，仅反问分支前触发）----

_LLM_DISAMBIG_SYSTEM = "你是电商产品消歧助手。只输出 JSON，不要输出任何其他内容。"
_LLM_DISAMBIG_PROMPT = """用户提到了同型号的多款产品，请根据用户当前消息与对话历史判断用户指的是哪一款。
只能从候选列表中选择；无法判断时 product_id 填 null。

候选列表：
{candidates}

对话历史（最近 4 条）：
{history}

用户当前消息：
{query}

输出严格 JSON（不要 markdown 代码块）：
{"product_id": "P001" 或 null}"""


def _parse_llm_product_id(raw: str, valid_ids: set[str]) -> str | None:
    """解析 LLM 兜底输出并做候选集全量校验（防幻觉：非候选 ID 一律视为 null）。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    data: Any = None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # 二次尝试：截取首个 { ... } 片段（LLM 可能夹带解释文字）
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                return None
    if not isinstance(data, dict):
        return None
    product_id = data.get("product_id")
    if isinstance(product_id, str) and product_id in valid_ids:
        return product_id
    return None


async def _llm_fallback(
    query: str, history: list[dict[str, str]] | None, enriched: list[dict]
) -> str | None:
    """LLM 兜底定向：命中候选返回 product_id；null/非法/失败返回 None（回落反问）。"""
    candidates_text = "\n".join(
        f"- {c['product_id']}: {c['name']}（{c['spec']}）；症状词：{'、'.join(c['symptom_keywords'])}"
        for c in enriched
    )
    history_text = "\n".join(
        f"{h.get('role', 'user')}: {h.get('content', '')}" for h in (history or [])[-_HISTORY_WINDOW:]
    )
    prompt = (
        _LLM_DISAMBIG_PROMPT.replace("{candidates}", candidates_text)
        .replace("{history}", history_text or "（无）")
        .replace("{query}", query)
    )
    try:
        raw = await call_llm(
            prompt,
            system=_LLM_DISAMBIG_SYSTEM,
            fast=True,  # 失败可降级（回落反问），快速失败不拖住主链路
        )
    except LLMUnavailableError as e:
        logger.warning("[Disambig] LLM 兜底不可用（回落反问）: %s", e)
        return None
    except Exception as e:  # 兜底路径任何异常都不能阻塞主链路
        logger.warning("[Disambig] LLM 兜底异常（回落反问）: %s", e)
        return None
    product_id = _parse_llm_product_id(raw, {c["product_id"] for c in enriched})
    if product_id:
        logger.info("[Disambig] LLM 兜底定向: %s", product_id)
    return product_id


async def disambiguate_product(
    query: str,
    history: list[dict[str, str]] | None = None,
    *,
    requester_user_id: str = "",
    requester_username: str = "",
) -> dict:
    """产品消歧管道入口（编排层 api/chat.py 在 faq/task 意图路由后调用）。

    Args:
        query: 用户当前输入
        history: 对话历史 [{role, content}]（可选），最近 4 条参与别名召回
        requester_user_id / requester_username: 请求者身份（订单层用；空则跳过订单层）

    Returns:
        {status: pass|resolved|clarify, method: none|signal|orders|llm|clarify,
         product_id, product_name, annotated_query, confirm_hint, candidates, clarify_text}
    """
    try:
        aliases = _load_models()
    except Exception as e:  # 防御：加载层意外异常不阻塞主链路
        logger.warning("[Disambig] 别名配置加载异常（按空配置处理）: %s", e)
        aliases = []
    if not aliases:
        return _result("pass", "none", query)

    recall = _recall_alias(query, history, aliases)
    if recall is None:
        return _result("pass", "none", query)
    entry, alias = recall
    if len(entry["products"]) <= 1:
        # 单候选直通：无歧义，行为与未上线一致（不注记、不注入）
        return _result("pass", "none", query)

    # 候选详情查询（失败剔除）：剩 1 个 → 直接定向；剩 0 个 → 回退直通
    enriched = await _fetch_candidates(entry["products"])
    if len(enriched) == 1:
        # 淘汰定向未经任何消歧信号层（详情失败剔除所致），method 记 none
        only = enriched[0]
        return _result(
            "resolved",
            "none",
            query,
            product_id=only["product_id"],
            product_name=only["name"],
            candidates=_candidates_payload(enriched, [0], None),
        )
    if not enriched:
        logger.warning("[Disambig] 别名 %s 候选详情全部查询失败，回退直通", alias)
        return _result("pass", "none", query)

    # ② 信号打分（当前 query；唯一最高分 >0 → signal）
    query_norm = _normalize(query)
    scores = _signal_scores(enriched, query_norm)
    max_score = max(scores)
    if max_score > 0 and scores.count(max_score) == 1:
        winner = enriched[scores.index(max_score)]
        return _result(
            "resolved",
            "signal",
            query,
            product_id=winner["product_id"],
            product_name=winner["name"],
            candidates=_candidates_payload(enriched, scores, None),
        )

    # ③ 订单交集（requester 身份；唯一交集 → orders；失败/身份空已由下层 warning 跳过）
    order_ids = await _order_product_ids(requester_user_id, requester_username)
    payload = _candidates_payload(enriched, scores, order_ids)
    if order_ids is not None:
        hits = [c for c in enriched if c["product_id"] in order_ids]
        if len(hits) == 1:
            return _result(
                "resolved",
                "orders",
                query,
                product_id=hits[0]["product_id"],
                product_name=hits[0]["name"],
                candidates=payload,
            )

    # ④ LLM 兜底（仅显式启用；默认关闭时零 LLM 调用）
    if settings.DISAMBIG_LLM_FALLBACK:
        llm_product_id = await _llm_fallback(query, history, enriched)
        if llm_product_id:
            winner = next(c for c in enriched if c["product_id"] == llm_product_id)
            return _result(
                "resolved",
                "llm",
                query,
                product_id=winner["product_id"],
                product_name=winner["name"],
                candidates=payload,
            )

    # ⑤ 反问（编排层短路 LLM/检索/工具，SSE 下发候选卡片）
    return _result(
        "clarify",
        "clarify",
        query,
        candidates=payload,
        clarify_text=_clarify_text(alias, enriched),
    )
