"""客服人格库（角色语气定制）单元测试（P2）。

覆盖：
- load_personas / list_personas：字段完整、system_prompt 不外泄
- persona_prompt：合法 id 返回语气指令；未知 / None 返回 None + warning（回落默认）
- personas.json 配置文件本体（3 个人格、字段齐全）

不连任何外部服务，纯配置加载测试。
"""

from __future__ import annotations

import json
import os

import app.core.personas as personas

_PERSONAS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "app", "data", "personas.json",
)


# ---------- 配置文件本体 ----------


def test_personas_file_has_three_personas_with_fields():
    """personas.json：3 个人格，name/description/system_prompt 齐全且非空。"""
    with open(_PERSONAS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 3
    for pid, cfg in data.items():
        assert cfg.get("name"), f"{pid} 缺 name"
        assert cfg.get("description"), f"{pid} 缺 description"
        assert cfg.get("system_prompt"), f"{pid} 缺 system_prompt"


# ---------- 加载与列表 ----------


def test_list_personas_exposes_metadata_without_prompt():
    """list_personas：暴露 id/name/description，不泄露 system_prompt。"""
    items = personas.list_personas()
    assert len(items) >= 3
    for item in items:
        assert item["id"]
        assert item["name"]
        assert item["description"]
        assert "system_prompt" not in item


# ---------- persona_prompt ----------


def test_persona_prompt_valid_id_returns_instruction():
    prompt = personas.persona_prompt("professional")
    assert prompt is not None
    assert "语气" in prompt


def test_persona_prompt_none_returns_none():
    assert personas.persona_prompt(None) is None


def test_persona_prompt_unknown_id_returns_none_with_warning(caplog):
    """未知 persona：返回 None 并 warning（回落默认语气，不 500）。"""
    result = personas.persona_prompt("no-such-persona")
    assert result is None
    assert any("未知" in r.message or "persona" in r.message for r in caplog.records)
