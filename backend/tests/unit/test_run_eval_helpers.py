"""run_eval 无 ground_truth 模式（feedback bad case 回流回归）帮助函数单元测试。

覆盖：
1. 全量样本含 ground_truth → 保留 context_recall
2. 存在无 ground_truth 样本（bad case 回流）→ 剔除 context_recall、保留其余
3. load_dataset：question 为空跳过、ground_truth 为空保留
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# scripts/run_eval.py 与 tests 不同目录，手动把 scripts 加入 import 路径
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from run_eval import _select_metrics, context_recall, load_dataset  # noqa: E402


def test_select_metrics_keeps_context_recall_when_all_ground_truth():
    """全部样本有标准答案：context_recall 保留。"""
    rows = [{"ground_truth": "答案1"}, {"ground_truth": "答案2"}]
    names = [m.name for m in _select_metrics(rows)]
    assert "context_recall" in names
    assert "faithfulness" in names


def test_select_metrics_drops_context_recall_for_badcase_rows():
    """存在无 ground_truth 样本（bad case 回流）：剔除 context_recall，保留其余。"""
    rows = [{"ground_truth": ""}, {"ground_truth": "有答案"}]
    metrics = _select_metrics(rows)
    assert context_recall not in metrics
    names = [m.name for m in metrics]
    assert "faithfulness" in names
    assert "context_precision" in names


def test_load_dataset_keeps_rows_without_ground_truth(tmp_path):
    """ground_truth 留空条目保留；question 为空条目跳过。"""
    fp = tmp_path / "data.json"
    fp.write_text(
        json.dumps(
            [
                {"question": "退货多久到账？", "ground_truth": ""},  # bad case 回流
                {"question": "", "ground_truth": "无问题跳过"},  # 无 question → 跳过
                {"question": "正常问题", "ground_truth": "正常答案"},
            ]
        ),
        encoding="utf-8",
    )
    rows = load_dataset(str(fp))
    assert len(rows) == 2
    assert rows[0]["question"] == "退货多久到账？"
    assert rows[0]["ground_truth"] == ""
    assert rows[1]["ground_truth"] == "正常答案"
