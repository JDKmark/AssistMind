"""电商实体识别单元测试。

覆盖：
- 规则层抽取：订单号 / 商品 ID / 无实体 / 手机号不误匹配
- 多轮历史提取：当前问题未命中时从历史回溯订单号
- 实体 → 工具参数补填（fill_tool_args）
- LLM 兜底（extract_with_llm）：命中 / 失败降级 / 开关关闭零调用 / 非法字段校验
"""

from __future__ import annotations

import asyncio

from app.core.infra.llm_factory import LLMUnavailableError
from app.core.mall.entity_extractor import (
    ENTITY_TO_TOOLS,
    extract,
    extract_with_llm,
    fill_tool_args,
)

# ---------- 规则层抽取 ----------

def test_extract_order_sn():
    """从问题中提取 11 位订单号（20 开头）。"""
    assert extract("查一下订单 20260801001")["order_sn"] == "20260801001"


def test_extract_order_sn_with_prefix():
    """订单号夹杂在句子中也能提取。"""
    assert extract("帮我看看订单号是20260801001的物流信息")["order_sn"] == "20260801001"


def test_extract_product_id():
    """提取 P+3 位数字商品 ID。"""
    assert extract("P001 有货吗")["product_id"] == "P001"


def test_extract_both_entities():
    """同一问题同时含订单号与商品 ID。"""
    result = extract("订单 20260801001 里的 P002 是什么规格")
    assert result["order_sn"] == "20260801001"
    assert result["product_id"] == "P002"


def test_extract_no_entity():
    """无实体返回三个 None（订单号/商品 ID/工单号）。"""
    result = extract("你们有退货政策吗")
    assert result == {"order_sn": None, "product_id": None, "ticket_id": None}


def test_extract_phone_number_not_matched():
    """手机号（11 位 1 开头）不误匹配为订单号。"""
    assert extract("我的手机号是 13812345678，帮我查物流")["order_sn"] is None


def test_extract_six_digit_not_matched():
    """6 位数字（非订单号格式）不匹配。"""
    assert extract("订单 999999 查一下")["order_sn"] is None


def test_extract_lowercase_p_not_matched():
    """小写 p 不匹配商品 ID（规格限定大写 P）。"""
    assert extract("有没有 p001 的测评")["product_id"] is None


def test_extract_product_id_adjacent_to_chinese():
    """商品 ID 紧贴中文也要能抽出来（真实提问的主流形态）。

    回归背景：旧正则用 (?<!\\w)/(?!\\w) 做边界，而 Python3 的 \\w 把中文也算单词
    字符，导致「商品P001缺货」「查下P001的库存」整条漏匹配，商品实体补填链路
    在最常见的句式上完全失效。边界必须只用 ASCII 字符类。
    """
    assert extract("商品P001缺货")["product_id"] == "P001"
    assert extract("帮我查下P001的库存")["product_id"] == "P001"
    assert extract("P001的尺码表在哪")["product_id"] == "P001"


def test_extract_product_id_still_rejects_glued_ascii():
    """放宽中文边界后，ASCII 粘连（SP001 / P0012）仍须拒绝。"""
    assert extract("型号SP001怎么样")["product_id"] is None
    assert extract("P0012 有货吗")["product_id"] is None


# ---------- 多轮历史提取 ----------

def test_extract_from_history_when_query_has_no_entity():
    """「物流到哪了」当前问题无实体，从历史提取订单号。"""
    result = extract(
        "物流到哪了？",
        history=[
            {"role": "user", "content": "查一下订单 20260801001"},
            {"role": "assistant", "content": "您的订单已发货。"},
        ],
    )
    assert result["order_sn"] == "20260801001"


def test_extract_current_query_takes_priority_over_history():
    """当前问题有实体时优先用当前，不用历史。"""
    result = extract(
        "查一下 20260801002",
        history=[
            {"role": "user", "content": "订单 20260801001 在哪"},
        ],
    )
    assert result["order_sn"] == "20260801002"


def test_extract_history_ignored_when_empty():
    """history 为空时退化为仅当前问题抽取。"""
    assert extract("物流到哪了？")["order_sn"] is None


def test_extract_history_takes_earliest_entity():
    """从历史倒序回溯，取最近一条含实体的消息。"""
    result = extract(
        "现在能退款吗",
        history=[
            {"role": "user", "content": "订单 20260801001 到哪了"},
            {"role": "assistant", "content": "已发货"},
            {"role": "user", "content": "那 20260801003 呢"},
        ],
    )
    assert result["order_sn"] == "20260801003"


# ---------- 实体 → 工具参数补填 ----------

def test_entity_to_tools_mapping():
    """订单号映射订单/物流/退款工具；商品 ID 映射商品工具。"""
    assert set(ENTITY_TO_TOOLS["order_sn"]) == {
        "query_order",
        "query_logistics",
        "apply_refund",
    }
    assert ENTITY_TO_TOOLS["product_id"] == ["query_product"]


def test_fill_tool_args_fills_missing_order_sn():
    """query_order 缺 order_sn 时用实体补填。"""
    args, filled = fill_tool_args(
        "query_order", {}, {"order_sn": "20260801001", "product_id": None}
    )
    assert args == {"order_sn": "20260801001"}
    assert filled is True


def test_fill_tool_args_keeps_llm_provided_value():
    """LLM 已给 order_sn 时不覆盖。"""
    args, filled = fill_tool_args(
        "query_order",
        {"order_sn": "20260801002"},
        {"order_sn": "20260801001", "product_id": None},
    )
    assert args["order_sn"] == "20260801002"
    assert filled is False


def test_fill_tool_args_product():
    """query_product 缺 product_id 时补填。"""
    args, filled = fill_tool_args(
        "query_product", {}, {"order_sn": None, "product_id": "P003"}
    )
    assert args == {"product_id": "P003"}
    assert filled is True


def test_fill_tool_args_unrelated_tool_untouched():
    """非业务工具（create_ticket）不补填。"""
    args, filled = fill_tool_args(
        "create_ticket", {"title": "t"}, {"order_sn": "20260801001", "product_id": None}
    )
    assert args == {"title": "t"}
    assert filled is False


def test_fill_tool_args_no_entity_no_change():
    """无实体时保持原参数、不标记补填。"""
    args, filled = fill_tool_args(
        "query_order", {}, {"order_sn": None, "product_id": None}
    )
    assert args == {}
    assert filled is False


# ---------- LLM 兜底（extract_with_llm）----------

async def _patch_llm(monkeypatch, responder):
    """开启 ENTITY_LLM_FALLBACK 开关并替换 call_llm，返回调用记录。"""
    monkeypatch.setattr(
        "app.core.mall.entity_extractor.settings.ENTITY_LLM_FALLBACK", True
    )
    calls: list[str] = []

    async def fake_call_llm(prompt, system=None, *, generation=False, fast=False):
        calls.append(prompt)
        result = responder(prompt)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    monkeypatch.setattr("app.core.mall.entity_extractor.call_llm", fake_call_llm)
    return calls


async def test_extract_with_llm_hit_when_rule_misses(monkeypatch):
    """规则未命中 + 兜底开启：LLM 返回合法 JSON → 提取实体。"""
    calls = await _patch_llm(
        monkeypatch, lambda prompt: '{"order_sn": "20260801001", "product_id": null}'
    )
    result = await extract_with_llm("帮我看下我买的那个东西到哪了")
    assert result["order_sn"] == "20260801001"
    assert len(calls) == 1


async def test_extract_with_llm_rule_hit_skips_llm(monkeypatch):
    """兜底开启但规则已命中：不调 LLM（规则层确定性优先）。"""
    calls = await _patch_llm(
        monkeypatch, lambda prompt: '{"order_sn": "99999999999", "product_id": "P099"}'
    )
    result = await extract_with_llm("查一下订单 20260801001 的物流")
    assert result["order_sn"] == "20260801001"
    assert calls == []


async def test_extract_with_llm_llm_unavailable_returns_empty(monkeypatch):
    """LLM provider 不可用：返回空实体 + 不抛异常（degraded 语义）。"""

    async def broken(prompt):
        raise LLMUnavailableError("all providers down")

    await _patch_llm(monkeypatch, broken)
    result = await extract_with_llm("你们运费怎么算")
    assert result == {"order_sn": None, "product_id": None, "ticket_id": None}


async def test_extract_with_llm_malformed_json_returns_empty(monkeypatch):
    """LLM 返回非 JSON：解析失败 → 空实体 + 不抛异常。"""
    await _patch_llm(monkeypatch, lambda prompt: "抱歉，我没法提取订单信息")
    result = await extract_with_llm("帮我查下售后进度")
    assert result == {"order_sn": None, "product_id": None, "ticket_id": None}


async def test_extract_with_llm_rejects_phone_number_from_llm(monkeypatch):
    """LLM 把手机号当订单号返回：规则正则校验拦截，按未提取处理（防幻觉）。"""
    await _patch_llm(
        monkeypatch, lambda prompt: '{"order_sn": "13812345678", "product_id": "P001"}'
    )
    result = await extract_with_llm("我手机号 13812345678，东西什么时候到")
    assert result["order_sn"] is None  # 手机号被拦截
    assert result["product_id"] == "P001"  # 合法字段仍保留


async def test_extract_with_llm_disabled_never_calls_llm(monkeypatch):
    """开关关闭：任何输入都不调 LLM（保持同步 extract 语义）。"""
    monkeypatch.setattr(
        "app.core.mall.entity_extractor.settings.ENTITY_LLM_FALLBACK", False
    )
    calls: list[str] = []

    async def spy(prompt):
        calls.append(prompt)
        return '{"order_sn": "20260801001", "product_id": null}'

    monkeypatch.setattr("app.core.mall.entity_extractor.call_llm", spy)
    result = await extract_with_llm("帮我看下我买的那个东西到哪了")
    assert result == {"order_sn": None, "product_id": None, "ticket_id": None}
    assert calls == []


async def test_extract_with_llm_uses_fast_mode(monkeypatch):
    """LLM 兜底以 fast=True 调用（失败可降级空实体，快速失败不拖住 Agent 主链路）。

    回归背景：实体兜底曾用标准 call_llm（失败感知长达分钟级），Agent 主链路
    会被 LLM 兜底慢调用拖住；fast → ~16s 内失败并返回空实体（degraded 语义）。
    """
    monkeypatch.setattr(
        "app.core.mall.entity_extractor.settings.ENTITY_LLM_FALLBACK", True
    )
    seen: list[bool] = []

    async def spy(prompt, system=None, *, generation=False, fast=False):
        seen.append(fast)
        return '{"order_sn": "20260801001", "product_id": null}'

    monkeypatch.setattr("app.core.mall.entity_extractor.call_llm", spy)
    result = await extract_with_llm("帮我看下我买的那个东西到哪了")
    assert result["order_sn"] == "20260801001"
    assert seen == [True]


# ---------- 工单号（ticket_id）抽取：TK- 正则 + 历史回溯 + get_ticket_status 补填 ----------
# 背景：查工单进度此前没有 TK 号抽取与参数补填链路，LLM 只能编号或反复 CLARIFY；
# 「提过工单后再问进度」的多轮指代完全依赖 LLM 自觉。


def test_extract_ticket_id_from_query():
    """当前消息含 TK- 工单号（14 位时间戳 + 7 位随机 = 21 位）直接抽取。"""
    result = extract("帮我查一下工单 TK-202609131030001234567 的进度")
    assert result["ticket_id"] == "TK-202609131030001234567"


def test_extract_ticket_id_lowercase_normalized():
    """小写 tk- 抽取后归一为大写（LLM 回显场景）。"""
    result = extract("tk-202609131030001234567 怎么样了")
    assert result["ticket_id"] == "TK-202609131030001234567"


def test_extract_ticket_id_longer_suffix():
    """随机后缀位数调大（TICKET_ID_RANDOM_SUFFIX>7）仍可抽取。"""
    result = extract("工单 TK-2026091310300012345678901 的状态")
    assert result["ticket_id"] == "TK-2026091310300012345678901"


def test_extract_ticket_id_boundary_guards():
    """边界防护：粘连字母/连字符前缀不误匹配，普通 TK 开头数字串过短不匹配。"""
    assert extract("xxTK-202609131030001234567")["ticket_id"] is None
    assert extract("ATK-202609131030001234567")["ticket_id"] is None
    # 20 位（时间戳 14 + 随机 6）低于 21 位下限：不匹配
    assert extract("TK-20260913103000123456")["ticket_id"] is None
    # 后缀含非数字不匹配
    assert extract("TK-20260913103000AB123")["ticket_id"] is None


def test_extract_ticket_id_adjacent_to_chinese():
    """工单号紧贴中文也要能抽出来（「工单TK-…状态」）。

    回归背景同商品 ID：旧正则 (?<![\\w-]) 被中文触发，导致「单号TK-…」
    「工单TK-…的状态」全部漏匹配，工单进度查询的参数补填失效。
    """
    assert extract("工单TK-202609131030001234567状态")["ticket_id"] == (
        "TK-202609131030001234567"
    )
    assert extract("单号TK-202609131030001234567")["ticket_id"] == (
        "TK-202609131030001234567"
    )


def test_extract_ticket_id_from_history_backtrack():
    """「我那个工单怎么样了」无 TK 号，从历史倒序回溯提取。"""
    result = extract(
        "我那个工单怎么样了",
        history=[
            {"role": "user", "content": "我提了个工单 TK-202609131030001234567"},
            {"role": "assistant", "content": "已收到您的工单，正在处理。"},
        ],
    )
    assert result["ticket_id"] == "TK-202609131030001234567"


def test_fill_tool_args_get_ticket_status():
    """get_ticket_status 缺 ticket_id 时由实体补填（ENTITY_TO_TOOLS 映射）。"""
    from app.core.mall.entity_extractor import fill_tool_args

    filled, filled_flag = fill_tool_args(
        "get_ticket_status", {}, {"ticket_id": "TK-202609131030001234567"}
    )
    assert filled == {"ticket_id": "TK-202609131030001234567"}
    assert filled_flag is True


async def test_parse_llm_entities_accepts_ticket_id():
    """LLM 兜底返回工单号：规则正则校验通过后保留（大小写归一）。"""
    from app.core.mall.entity_extractor import _parse_llm_entities

    result = _parse_llm_entities(
        '{"order_sn": null, "product_id": null, "ticket_id": "tk-202609131030001234567"}'
    )
    assert result["ticket_id"] == "TK-202609131030001234567"


async def test_parse_llm_entities_rejects_bad_ticket_id():
    """LLM 兜底返回非法工单号（过短/带字母）：正则校验拦截。"""
    from app.core.mall.entity_extractor import _parse_llm_entities

    result = _parse_llm_entities(
        '{"order_sn": null, "product_id": null, "ticket_id": "TK-123"}'
    )
    assert result["ticket_id"] is None
