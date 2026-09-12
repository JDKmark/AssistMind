"""客服人格库（角色语气定制，提示词层）。

personas.json 外置于 app/data/（与 intent_routes.json 同风格），mtime 热加载：
修改配置后下次请求即生效。人格只改「语气」，由调用方拼在既有 system prompt
之后，不覆盖客服职责与 RAG 事实性约束。

「客服语气定制」的提示词层落地：语气一致性可用提示词稳定达成，
知识问答准确性靠 RAG——「何时该微调 vs 提示词」的判断依据：
语气属表层生成风格，提示词即可稳定约束（详见下方 _assemble_prompt），
LoRA 微调仅在出现领域知识/格式强对齐需求时才考虑。
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# app/core/personas.py → app/data/personas.json
_PERSONAS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "personas.json")

# 模块级缓存：按 json mtime 热加载（与 router/intent.py 同模式）
_personas_cache: dict = {"mtime": None, "data": None}

# voice card 新 schema 的特征字段：任一存在即按结构化人格卡组装
# （personas.json 同时保留旧 system_prompt 字段作回滚锚点，新字段优先）
_VOICE_CARD_KEYS = ("tone", "rules", "few_shots")

# tone 标尺（1-5）→ 定位描述：formal 越高越正式、playful 越高越俏皮、
# empathy 越高越强调共情；三个人格取值需拉开区分度
_TONE_DESCRIPTORS: dict[str, dict[int, str]] = {
    "formal": {1: "非常轻松随意", 2: "偏轻松", 3: "张弛有度", 4: "偏正式", 5: "非常正式严谨"},
    "playful": {1: "不使用俏皮表达", 2: "略带亲和", 3: "适度活泼", 4: "比较俏皮", 5: "热情俏皮、互动感强"},
    "empathy": {1: "以事实陈述为主", 2: "适度关照情绪", 3: "兼顾情绪与事实", 4: "比较强调共情", 5: "高度共情，先安抚情绪再解决问题"},
}
_TONE_LABELS = {"formal": "正式度", "playful": "俏皮度", "empathy": "共情度"}

# 情绪安全阀（规则层，无模型）：负面情绪关键词，任一命中即触发共情优先
_NEGATIVE_KEYWORDS = ["投诉", "气死", "垃圾", "太差", "再也不", "曝光", "赔偿", "欺骗"]

# 连续感叹号检测：！/! 混排连续出现 ≥2 次视为情绪激动
_EXCLAMATION_RUN = re.compile(r"[！!]{2,}")

_EMPATHY_OVERRIDE_TEXT = (
    "用户当前情绪明显负面，本轮回答优先共情安抚（先理解与道歉，再给解决方案），"
    "收敛俏皮与营销化表达；所有人格语气要求在本轮让位于该原则。"
)


def load_personas() -> dict[str, dict]:
    """加载人格库（mtime 热加载；文件异常时返回上次缓存或空 dict）。"""
    try:
        mtime = os.path.getmtime(_PERSONAS_PATH)
    except OSError:
        logger.warning("[Personas] 配置文件不可读: %s", _PERSONAS_PATH)
        return _personas_cache.get("data") or {}
    if _personas_cache["mtime"] != mtime or _personas_cache["data"] is None:
        try:
            with open(_PERSONAS_PATH, encoding="utf-8") as f:
                _personas_cache["data"] = json.load(f)
            _personas_cache["mtime"] = mtime
        except (OSError, ValueError) as e:
            # 解析失败保留旧缓存（人格失效只影响语气，不影响可用性）
            logger.warning("[Personas] 配置解析失败（沿用旧缓存）: %s", e)
            return _personas_cache.get("data") or {}
    return _personas_cache["data"]


def list_personas() -> list[dict]:
    """人格元信息列表（前端选择器数据源）；不暴露 system_prompt。"""
    return [
        {
            "id": pid,
            "name": cfg.get("name", pid),
            "description": cfg.get("description", ""),
        }
        for pid, cfg in load_personas().items()
    ]


def _assemble_prompt(cfg: dict) -> str:
    """把 voice card（tone 标尺 + do/don't 清单 + few-shot 示例）组装成语气指令段。

    组装顺序：身份定位 → tone 三维度标尺描述 → 应当做到 → 禁止 → 示例。
    各维度缺省时跳过对应段落（配置异常不致命，不抛异常）。
    """
    parts: list[str] = []
    name = cfg.get("name", "")
    description = cfg.get("description", "")
    if name:
        suffix = f"（{description}）" if description else ""
        parts.append(f"语气要求：你以「{name}」的身份与用户交流{suffix}。")

    tone = cfg.get("tone") or {}
    tone_bits = []
    for dim in ("formal", "playful", "empathy"):
        if dim in tone:
            try:
                level = max(1, min(5, int(tone[dim])))
            except (TypeError, ValueError):
                # 脏配置跳过该维度（语气缺失只影响风格，不影响可用性）
                continue
            label = _TONE_LABELS[dim]
            desc = _TONE_DESCRIPTORS[dim][level]
            tone_bits.append(f"{label} {level}/5（{desc}）")
    if tone_bits:
        parts.append("语气定位：" + "、".join(tone_bits) + "。")

    rules = cfg.get("rules") or {}
    do_rules = rules.get("do") or []
    dont_rules = rules.get("dont") or []
    if do_rules:
        parts.append("应当做到：" + "；".join(do_rules) + "。")
    if dont_rules:
        parts.append("禁止：" + "；".join(dont_rules) + "。")

    few_shots = cfg.get("few_shots") or []
    for shot in few_shots:
        q = shot.get("q", "")
        a = shot.get("a", "")
        if q and a:
            parts.append(f"示例：用户问「{q}」→ 回答「{a}」")

    return "\n".join(parts)


def emotion_override(query: str) -> str | None:
    """规则层情绪检测：命中返回共情优先指令，否则 None。

    触发条件（任一）：命中 _NEGATIVE_KEYWORDS；或 ！/! 连续出现 ≥2 次。
    人格让位：调用方把返回值拼在 persona 指令之前（人格语气在本轮让位）。
    """
    if not query:
        return None
    if any(kw in query for kw in _NEGATIVE_KEYWORDS):
        return _EMPATHY_OVERRIDE_TEXT
    if _EXCLAMATION_RUN.search(query):
        return _EMPATHY_OVERRIDE_TEXT
    return None


def persona_prompt(persona_id: str | None) -> str | None:
    """按 id 取人格语气指令；空或未知返回 None（调用方回落默认语气）。

    新 schema（voice card：tone/rules/few_shots）走 _assemble_prompt 组装；
    老 schema（仅 system_prompt 字段）直接返回该字段——向后兼容，便于回滚。
    未知 id 记 warning（不静默）：便于发现前端传错或配置回退。
    """
    if not persona_id:
        return None
    cfg = load_personas().get(persona_id)
    if cfg is None:
        logger.warning("[Personas] 未知 persona=%s，回落默认语气", persona_id)
        return None
    if any(key in cfg for key in _VOICE_CARD_KEYS):
        return _assemble_prompt(cfg) or None
    return cfg.get("system_prompt") or None
