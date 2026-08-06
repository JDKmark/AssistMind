"""评估数据集 eval_mall_qa.json 静态校验。

不 mock：直接读取 backend/app/data/eval_mall_qa.json 做静态校验，
保证 mall 知识库评估（scripts/run_eval.py 复用）的数据集可用：
- 能解析为 JSON 数组
- 条目数 >= 30（电商客服链路完成后的数据量门槛）
- 每条含非空 question / ground_truth
- adversarial 字段为 bool
- 至少 3 条 adversarial=true（对抗样本）
- 客服向占比过半（按 5 大业务域关键词分组统计）
- 5 大业务域全覆盖（商品咨询/订单/售后/物流/优惠与会员，每域 >= 3 条）
"""

from __future__ import annotations

import json
from pathlib import Path

EVAL_MALL_QA_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "data" / "eval_mall_qa.json"
)

# 客服业务 5 大域关键词分组（仅匹配 question 文本）：
# 刻意避开运维向条目（如「mall 的订单表 oms_order 是做什么的」）会命中的
# 单字宽泛词（"订单""发货"），用客服语境下的组合词/编号锚定，
# 保证分组只统计电商客服向样本。
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "商品咨询": ["多少钱", "价格", "库存", "还有货", "有货吗", "规格", "P001", "P002", "P003", "P004", "P005", "售价", "什么价"],
    "订单": ["订单状态", "查订单", "订单 2024", "我的订单", "订单号"],
    "售后": ["退货", "退款", "售后", "无理由"],
    "物流": ["物流", "发货", "快递", "送达"],
    "优惠与会员": ["优惠券", "满减", "积分", "会员"],
}


def _load_eval_mall_qa() -> list[dict]:
    """读取并解析 eval_mall_qa.json，返回条目列表（非数组时抛断言）。"""
    with EVAL_MALL_QA_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list), "eval_mall_qa.json 必须是 JSON 数组"
    return data


def _matched_domains(question: str) -> set[str]:
    """返回 question 命中的客服域集合（关键词分组）。"""
    return {name for name, kws in DOMAIN_KEYWORDS.items() if any(k in question for k in kws)}


def test_eval_mall_qa_is_json_array():
    """eval_mall_qa.json 能解析为 JSON 数组。"""
    data = _load_eval_mall_qa()
    assert isinstance(data, list)


def test_eval_mall_qa_has_enough_entries():
    """条目数 >= 30。"""
    assert len(_load_eval_mall_qa()) >= 30


def test_eval_mall_qa_each_has_nonempty_question_and_ground_truth():
    """每条含非空 question / ground_truth。"""
    for i, item in enumerate(_load_eval_mall_qa()):
        assert isinstance(item, dict), f"第 {i} 条不是对象"
        question = item.get("question")
        ground_truth = item.get("ground_truth")
        assert isinstance(question, str) and question.strip(), f"第 {i} 条 question 为空"
        assert isinstance(ground_truth, str) and ground_truth.strip(), (
            f"第 {i} 条 ground_truth 为空"
        )


def test_eval_mall_qa_fields_are_complete():
    """每条字段完整：恰好含 question / ground_truth / adversarial 三个字段。"""
    for i, item in enumerate(_load_eval_mall_qa()):
        assert set(item.keys()) == {"question", "ground_truth", "adversarial"}, (
            f"第 {i} 条字段不完整（实际 {sorted(item.keys())}）"
        )


def test_eval_mall_qa_adversarial_field_is_bool():
    """每条 adversarial 字段为 bool。"""
    for i, item in enumerate(_load_eval_mall_qa()):
        assert isinstance(item.get("adversarial"), bool), f"第 {i} 条 adversarial 非 bool"


def test_eval_mall_qa_has_enough_adversarial_cases():
    """至少 3 条 adversarial=true（对抗样本）。"""
    data = _load_eval_mall_qa()
    n = sum(1 for item in data if item.get("adversarial") is True)
    assert n >= 3


def test_eval_mall_qa_customer_service_ratio_over_half():
    """客服向占比过半：命中任一客服域关键词的条目数 > 总数一半。"""
    data = _load_eval_mall_qa()
    n_cs = sum(1 for item in data if _matched_domains(item["question"]))
    assert n_cs > len(data) / 2, (
        f"客服向样本占比不足一半: {n_cs}/{len(data)}"
    )


def test_eval_mall_qa_covers_five_domains():
    """5 大客服域全覆盖：每域至少 3 条命中关键词分组。"""
    data = _load_eval_mall_qa()
    for domain, kws in DOMAIN_KEYWORDS.items():
        hits = [
            item["question"]
            for item in data
            if any(k in item["question"] for k in kws)
        ]
        assert len(hits) >= 3, f"客服域「{domain}」覆盖不足 3 条（实际 {len(hits)} 条）"
