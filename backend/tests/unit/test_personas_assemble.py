"""人格 voice card 组装单元测试（persona-upgrade T3）。

覆盖：
- 新 schema（tone/rules/few_shots）组装结果：含身份定位、tone 标尺描述、
  do/don't 清单、few-shot 示例（按「示例：用户问「…」→ 回答「…」」拼接）
- 老 schema（仅 system_prompt 字段）直通，不走组装——向后兼容便于回滚
- 未知 persona 返回 None + warning（不静默）
- 配置本体：三个人格 tone 标尺拉开区分度、few_shots ≥2 条

不连任何外部服务，纯配置/纯函数测试。
"""

from __future__ import annotations

import json
import os

import app.core.personas as personas

_PERSONAS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "app", "data", "personas.json",
)


# ---------- 新 schema 组装 ----------


def test_new_schema_prompt_contains_all_voice_card_parts():
    """lively（新 schema）：组装结果含 tone 定位 / do / don't / few-shot 四要素。"""
    prompt = personas.persona_prompt("lively")
    assert prompt is not None
    # 身份定位（含人格名，保证 "活泼" 等人格特征可被下游断言）
    assert "活泼客服" in prompt
    # tone 标尺翻译为定位描述（playful=5 拉满）
    assert "语气定位" in prompt
    assert "俏皮度 5/5" in prompt
    # do 清单
    assert "应当做到" in prompt
    assert "每条回答最多一处语气词" in prompt
    # don't 清单
    assert "禁止" in prompt
    assert "必须严谨" in prompt
    # few-shot 示例拼接格式
    assert "示例：用户问「运费谁出？」→ 回答「" in prompt
    # 组装结果不应原样返回旧 system_prompt 字段（新字段优先）
    assert "保持热情活泼的客服语气" not in prompt


def test_new_schema_prompt_starts_with_tone_requirement():
    """组装结果以「语气要求：」开头（既有用例断言「语气」字样依赖此约定）。"""
    for pid in ("professional", "gentle", "lively"):
        prompt = personas.persona_prompt(pid)
        assert prompt is not None
        assert prompt.startswith("语气要求：")


def test_each_persona_few_shots_distinct_between_personas():
    """同问题不同人格的 few-shot 答案不同（退款时效三个人格都示例了，语气各异）。"""
    prompts = {pid: personas.persona_prompt(pid) for pid in ("professional", "gentle", "lively")}
    assert all(prompts.values())
    # 各人格组装结果互不相同（tone/do/dont/few-shot 拉开区分度）
    assert len(set(prompts.values())) == 3


# ---------- 老 schema 向后兼容 ----------


def test_old_schema_system_prompt_passthrough(monkeypatch):
    """老 schema（仅 system_prompt 字段）直接返回该字段，不走组装。"""
    legacy = {
        "legacy": {
            "name": "旧人格",
            "description": "回滚用",
            "system_prompt": "旧版语气指令原文",
        }
    }
    monkeypatch.setattr(personas, "load_personas", lambda: legacy)
    assert personas.persona_prompt("legacy") == "旧版语气指令原文"


def test_empty_system_prompt_returns_none(monkeypatch):
    """老 schema 但 system_prompt 为空：返回 None（回落默认语气），不抛异常。"""
    legacy = {"legacy": {"name": "旧人格", "system_prompt": ""}}
    monkeypatch.setattr(personas, "load_personas", lambda: legacy)
    assert personas.persona_prompt("legacy") is None


# ---------- 未知 / 空 persona ----------


def test_unknown_persona_returns_none_with_warning(caplog):
    """未知 persona：返回 None 并 warning（回落默认语气，不 500）。"""
    result = personas.persona_prompt("no-such-persona")
    assert result is None
    assert any("未知" in r.message and "persona" in r.message for r in caplog.records)


def test_none_persona_returns_none():
    assert personas.persona_prompt(None) is None


# ---------- 配置本体约束（voice card 结构）----------


def test_personas_tone_scales_well_separated():
    """三个人格 tone 标尺拉开区分度：professional 最正式、lively 最俏皮、gentle 共情最高。"""
    with open(_PERSONAS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    professional = data["professional"]["tone"]
    gentle = data["gentle"]["tone"]
    lively = data["lively"]["tone"]
    assert professional["formal"] > lively["formal"]
    assert lively["playful"] > gentle["playful"] > professional["playful"]
    assert gentle["empathy"] > professional["empathy"]


def test_personas_few_shots_at_least_two_fact_aligned():
    """每人格 few_shots ≥2 条；退款时效等事实与业务政策文档口径一致。"""
    with open(_PERSONAS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    for pid, cfg in data.items():
        shots = cfg.get("few_shots") or []
        assert len(shots) >= 2, f"{pid} few_shots 不足 2 条"
        for shot in shots:
            assert shot.get("q"), f"{pid} few_shot 缺 q"
            assert shot.get("a"), f"{pid} few_shot 缺 a"
    # 事实对齐 knowledge/mall/business：7 天无理由、3 个工作日退款、48 小时发货
    all_text = json.dumps(data, ensure_ascii=False)
    assert "7 天" in all_text
    assert "3 个工作日" in all_text
    assert "48 小时" in all_text
