"""评估数据集 eval_qa.json 静态校验。

不 mock：直接读取 backend/app/data/eval_qa.json 做静态校验，
保证评估脚本（scripts/run_eval.py）的数据集可用：
- 能解析为 JSON 数组
- 条目数 >= 10
- 每条含非空 question / ground_truth
- adversarial 字段为 bool
- 至少 3 条 adversarial=true（对抗样本）
"""

from __future__ import annotations

import json
from pathlib import Path

EVAL_QA_PATH = Path(__file__).resolve().parents[2] / "app" / "data" / "eval_qa.json"


def _load_eval_qa() -> list[dict]:
    """读取并解析 eval_qa.json，返回条目列表（非数组时抛断言）。"""
    with EVAL_QA_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list), "eval_qa.json 必须是 JSON 数组"
    return data


def test_eval_qa_is_json_array():
    """eval_qa.json 能解析为 JSON 数组。"""
    data = _load_eval_qa()
    assert isinstance(data, list)


def test_eval_qa_has_enough_entries():
    """条目数 >= 10。"""
    assert len(_load_eval_qa()) >= 10


def test_eval_qa_each_has_nonempty_question_and_ground_truth():
    """每条含非空 question / ground_truth。"""
    for i, item in enumerate(_load_eval_qa()):
        assert isinstance(item, dict), f"第 {i} 条不是对象"
        question = item.get("question")
        ground_truth = item.get("ground_truth")
        assert isinstance(question, str) and question.strip(), f"第 {i} 条 question 为空"
        assert isinstance(ground_truth, str) and ground_truth.strip(), (
            f"第 {i} 条 ground_truth 为空"
        )


def test_eval_qa_adversarial_field_is_bool():
    """每条 adversarial 字段为 bool。"""
    for i, item in enumerate(_load_eval_qa()):
        assert isinstance(item.get("adversarial"), bool), f"第 {i} 条 adversarial 非 bool"


def test_eval_qa_has_enough_adversarial_cases():
    """至少 3 条 adversarial=true。"""
    data = _load_eval_qa()
    n = sum(1 for item in data if item.get("adversarial") is True)
    assert n >= 3
