"""产品消歧管道单元测试（规则层，不依赖 Docker / 真实服务）。

覆盖 spec 第 5 章「型号别名配置热加载 / 产品消歧管道 / LLM 兜底消歧」全部 Scenario：
- 无别名直通 / 单候选直通（原链路零改动，不调数据源）
- 归一化命中（s1pro / S1  PRO）
- 信号唯一定向（层序约束：信号先于订单） / 订单交集定向 / 双持有无信号反问
- 反问后回复定向（历史含别名） / 多轮排障沿用定向
- my_orders 异常降级（断言 logger.warning） / 候选详情异常剔除
- 配置热加载 / 非法配置沿用旧配置 / 配置文件缺失为空
- LLM 兜底四态（默认关闭零调用 / 成功定向 / 输出非候选视为 null / 调用失败降级）

mock 策略：monkeypatch 替换 product_disambiguator.mall_ds（query_product/my_orders）
与 call_llm；配置文件指向 tmp_path（mtime 热加载可测）。
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.core.mall import product_disambiguator as disamb

# 基线别名配置（S1 Pro 双候选 + 单候选别名 AirPods，覆盖直通场景）
BASE_CONFIG = {
    "aliases": [
        {
            "alias": "S1 Pro",
            "products": [
                {
                    "product_id": "P006",
                    "symptom_keywords": ["吸奶器", "吸奶", "吸不出", "奶水", "奶阵", "涨奶"],
                },
                {
                    "product_id": "P007",
                    "symptom_keywords": ["扫地机器人", "扫地", "拖地", "尘盒", "基站", "边刷"],
                },
            ],
        },
        {
            "alias": "AirPods",
            "products": [
                {"product_id": "P004", "symptom_keywords": ["降噪", "耳机"]},
            ],
        },
    ]
}

PRODUCT_DETAILS = {
    "P004": {"id": "P004", "name": "Apple AirPods Pro", "spec": "Pro", "price": 1899, "stock_status": "有货"},
    "P006": {"id": "P006", "name": "贝亲 S1 Pro 电动吸奶器", "spec": "双边电动 静音款", "price": 1299, "stock_status": "有货"},
    "P007": {"id": "P007", "name": "追觅 S1 Pro 扫地机器人", "spec": "自集尘 拖扫一体", "price": 2999, "stock_status": "有货"},
}

# 演示订单持有：user1 仅持 P006（交集唯一）；user2 同时持 P006+P007（交集歧义）
ORDERS_BY_USER = {
    "user1": [{"items": [{"product_id": "P006"}]}],
    "user2": [{"items": [{"product_id": "P006"}, {"product_id": "P007"}]}],
}

CLARIFY_HISTORY = [
    {
        "role": "assistant",
        "content": "S1 Pro 有两款产品：贝亲 S1 Pro 电动吸奶器（双边电动 静音款）"
        "和追觅 S1 Pro 扫地机器人（自集尘 拖扫一体）。请问您说的是哪一款？",
    }
]


class _FakeMallDS:
    """mall 数据源桩：固定商品详情与订单持有，记录调用供零调用断言。"""

    def __init__(self, products=None, orders=None, product_error_ids=(), orders_error=False):
        self.products = products if products is not None else PRODUCT_DETAILS
        self.orders = orders if orders is not None else ORDERS_BY_USER
        self.product_error_ids = set(product_error_ids)
        self.orders_error = orders_error
        self.product_calls: list[str] = []
        self.orders_calls: list[tuple[str, str]] = []

    async def query_product(self, product_id: str, *, requester_role: str):
        self.product_calls.append(product_id)
        if product_id in self.product_error_ids:
            raise RuntimeError(f"product source down: {product_id}")
        return self.products.get(product_id)

    async def my_orders(self, *, requester_user_id, requester_username, status=None, limit=50, offset=0):
        self.orders_calls.append((requester_user_id, requester_username))
        if self.orders_error:
            raise RuntimeError("order source down")
        rows = self.orders.get(requester_username, [])
        return {"orders": rows, "total": len(rows)}


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """每个用例独立配置文件（mtime 热加载可测）+ 模块缓存复位。"""
    cfg = tmp_path / "product_models.json"
    cfg.write_text(json.dumps(BASE_CONFIG, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(disamb, "_MODELS_PATH", str(cfg))
    disamb._models_cache["mtime"] = None
    disamb._models_cache["data"] = None
    yield cfg
    disamb._models_cache["mtime"] = None
    disamb._models_cache["data"] = None


@pytest.fixture(autouse=True)
def _default_settings(monkeypatch):
    """默认关闭 LLM 兜底（与线上默认一致）；兜底用例自行覆盖。"""
    monkeypatch.setattr(disamb, "settings", Settings(DISAMBIG_LLM_FALLBACK=False))


def _install_ds(monkeypatch, **kwargs) -> _FakeMallDS:
    fake = _FakeMallDS(**kwargs)
    monkeypatch.setattr(disamb, "mall_ds", fake)
    return fake


# ---------- 直通场景 ----------


async def test_no_alias_mention_passes_without_data_source_calls(monkeypatch):
    """无别名提及直通：status=pass、annotated_query 等于原 query、不调用任何数据源。"""
    fake = _install_ds(monkeypatch)
    result = await disamb.disambiguate_product("华为 Mate 70 Pro 多少钱")
    assert result["status"] == "pass"
    assert result["method"] == "none"
    assert result["annotated_query"] == "华为 Mate 70 Pro 多少钱"
    assert result["product_id"] is None
    assert result["confirm_hint"] is False
    assert result["candidates"] == []
    assert fake.product_calls == []
    assert fake.orders_calls == []


async def test_single_candidate_alias_passes(monkeypatch):
    """单候选直通：别名仅映射 1 个商品 → pass（不注记、不注入）。"""
    _install_ds(monkeypatch)
    result = await disamb.disambiguate_product("AirPods 降噪效果怎么样")
    assert result["status"] == "pass"
    assert result["annotated_query"] == "AirPods 降噪效果怎么样"
    # 直通语义：未进入消歧管道，不取商品详情
    assert disamb.mall_ds.product_calls == []


# ---------- 归一化与召回 ----------


@pytest.mark.parametrize("query", ["我的s1pro不吸了", "S1  PRO 不吸了", "Ｓ１　ＰＲＯ"])
async def test_normalized_recall_hits_all_candidates(monkeypatch, query):
    """归一化命中：s1pro（去空格）/ S1  PRO（空白折叠）/ 全角（NFKC）均召回全部候选。"""
    fake = _install_ds(monkeypatch)
    result = await disamb.disambiguate_product(query, requester_user_id="uid-u", requester_username="u")
    # 双持有 + 无信号 → 反问（能到反问即说明召回命中了全部候选）
    assert result["status"] == "clarify"
    assert {c["product_id"] for c in result["candidates"]} == {"P006", "P007"}
    assert set(fake.product_calls) == {"P006", "P007"}


async def test_history_alias_recall_multi_turn_troubleshooting(monkeypatch):
    """多轮排障沿用定向：query「还是不吸了」无别名无信号，历史含别名 → 订单交集 resolved P006。"""
    _install_ds(monkeypatch)
    result = await disamb.disambiguate_product(
        "还是不吸了",
        history=CLARIFY_HISTORY + [{"role": "user", "content": "我的 S1 Pro 不吸了"}],
        requester_user_id="uid-user1",
        requester_username="user1",
    )
    assert result["status"] == "resolved"
    assert result["method"] == "orders"
    assert result["product_id"] == "P006"
    assert result["product_name"] == "贝亲 S1 Pro 电动吸奶器"
    assert result["annotated_query"] == "还是不吸了 贝亲 S1 Pro 电动吸奶器"
    assert result["confirm_hint"] is True


# ---------- 信号 / 订单分层 ----------


async def test_signal_resolves_for_dual_holder(monkeypatch):
    """信号唯一定向：user2（双持有）「S1 Pro 吸不出奶了」→ resolved/signal/P006。"""
    _install_ds(monkeypatch)
    result = await disamb.disambiguate_product(
        "S1 Pro 吸不出奶了", requester_user_id="uid-user2", requester_username="user2"
    )
    assert result["status"] == "resolved"
    assert result["method"] == "signal"
    assert result["product_id"] == "P006"


async def test_signal_beats_orders_for_single_holder(monkeypatch):
    """层序约束：信号先于订单——user1 仅持 P006，反问后回复点名扫地机器人 → P007（非订单持有款）。"""
    _install_ds(monkeypatch)
    result = await disamb.disambiguate_product(
        "扫地机器人，不吸了",
        history=CLARIFY_HISTORY,
        requester_user_id="uid-user1",
        requester_username="user1",
    )
    assert result["status"] == "resolved"
    assert result["method"] == "signal"
    assert result["product_id"] == "P007"


async def test_orders_intersection_resolves_for_single_holder(monkeypatch):
    """订单交集定向：user1（仅持 P006）「我的 S1 Pro 不吸了」（无信号词）→ resolved/orders/P006。"""
    fake = _install_ds(monkeypatch)
    result = await disamb.disambiguate_product(
        "我的 S1 Pro 不吸了", requester_user_id="uid-user1", requester_username="user1"
    )
    assert result["status"] == "resolved"
    assert result["method"] == "orders"
    assert result["product_id"] == "P006"
    assert result["annotated_query"] == "我的 S1 Pro 不吸了 贝亲 S1 Pro 电动吸奶器"
    # 订单层以 requester 身份查询
    assert fake.orders_calls == [("uid-user1", "user1")]


async def test_clarify_for_dual_holder_without_signal(monkeypatch):
    """双持有且无信号触发反问：clarify_text 含两款商品名称与 spec。"""
    _install_ds(monkeypatch)
    result = await disamb.disambiguate_product(
        "我的 S1 Pro 不吸了", requester_user_id="uid-user2", requester_username="user2"
    )
    assert result["status"] == "clarify"
    assert result["method"] == "clarify"
    assert result["product_id"] is None
    assert result["clarify_text"]
    for c in result["candidates"]:
        assert c["product_id"] in {"P006", "P007"}
        assert c["name"] and c["spec"]
        assert c["in_orders"] is True  # 订单层已运行，双款均持有
    assert "贝亲 S1 Pro 电动吸奶器" in result["clarify_text"]
    assert "双边电动 静音款" in result["clarify_text"]
    assert "追觅 S1 Pro 扫地机器人" in result["clarify_text"]
    assert "自集尘 拖扫一体" in result["clarify_text"]


async def test_reply_after_clarify_resolves_by_signal(monkeypatch):
    """反问后回复定向：历史含上一轮反问（含别名），回复「扫地机器人，不吸了」→ P007（signal）。"""
    _install_ds(monkeypatch)
    result = await disamb.disambiguate_product(
        "扫地机器人，不吸了",
        history=CLARIFY_HISTORY,
        requester_user_id="uid-user2",
        requester_username="user2",
    )
    assert result["status"] == "resolved"
    assert result["method"] == "signal"
    assert result["product_id"] == "P007"


# ---------- 降级路径 ----------


async def test_my_orders_failure_degrades_to_clarify_with_warning(monkeypatch, caplog):
    """my_orders 异常降级：跳过订单层 + logger.warning，最终 clarify，不抛异常。"""
    _install_ds(monkeypatch, orders_error=True)
    with caplog.at_level(logging.WARNING, logger="app.core.mall.product_disambiguator"):
        result = await disamb.disambiguate_product(
            "我的 S1 Pro 不吸了", requester_user_id="uid-user1", requester_username="user1"
        )
    assert result["status"] == "clarify"
    assert any("my_orders" in r.message or "订单层" in r.message for r in caplog.records)


async def test_empty_identity_skips_orders_layer_with_warning(monkeypatch, caplog):
    """身份为空跳过订单层（warning），无信号时反问。"""
    _install_ds(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="app.core.mall.product_disambiguator"):
        result = await disamb.disambiguate_product("我的 S1 Pro 不吸了")
    assert result["status"] == "clarify"
    assert disamb.mall_ds.orders_calls == []
    assert caplog.records


async def test_candidate_detail_failure_eliminated_resolves_remaining(monkeypatch):
    """候选详情失败剔除：P006 查询异常被剔除后仅剩 P007 → resolved 且 product_id=P007。"""
    _install_ds(monkeypatch, product_error_ids={"P006"})
    result = await disamb.disambiguate_product("我的 S1 Pro 不吸了")
    assert result["status"] == "resolved"
    assert result["product_id"] == "P007"
    assert result["product_name"] == "追觅 S1 Pro 扫地机器人"


async def test_all_candidate_details_fail_falls_back_to_pass(monkeypatch, caplog):
    """候选详情全部失败：剩 0 个 → pass + warning，不抛异常。"""
    _install_ds(monkeypatch, product_error_ids={"P006", "P007"})
    with caplog.at_level(logging.WARNING, logger="app.core.mall.product_disambiguator"):
        result = await disamb.disambiguate_product("我的 S1 Pro 不吸了")
    assert result["status"] == "pass"
    assert result["annotated_query"] == "我的 S1 Pro 不吸了"
    assert caplog.records


# ---------- 配置热加载 ----------


async def test_config_hot_reload_takes_effect_immediately(monkeypatch, _isolated_config):
    """修改配置（mtime 变化）后即时生效，无需重启。"""
    _install_ds(monkeypatch)
    first = await disamb.disambiguate_product("AirPods 音质怎么样")
    assert first["status"] == "pass"  # 单候选别名直通

    new_config = {
        "aliases": [
            {
                "alias": "AirPods",
                "products": [
                    {"product_id": "P004", "symptom_keywords": ["降噪"]},
                    {"product_id": "P006", "symptom_keywords": ["耳机"]},
                ],
            }
        ]
    }
    _isolated_config.write_text(json.dumps(new_config, ensure_ascii=False), encoding="utf-8")
    # 显式推进 mtime（部分文件系统 mtime 秒级精度，同秒重写不触发热加载）
    st = _isolated_config.stat()
    import os

    os.utime(_isolated_config, (st.st_atime, st.st_mtime + 10))

    second = await disamb.disambiguate_product("AirPods 音质怎么样")
    assert second["status"] == "clarify"  # 变为双候选（无信号词）→ 进入消歧反问


async def test_invalid_config_keeps_last_valid(monkeypatch, _isolated_config, caplog):
    """非法 JSON：沿用修改前的有效配置并记录 warning，返回结果不异常。"""
    _install_ds(monkeypatch)
    first = await disamb.disambiguate_product("AirPods 降噪怎么样")
    assert first["status"] == "pass"

    import os

    _isolated_config.write_text("{invalid json", encoding="utf-8")
    st = _isolated_config.stat()
    os.utime(_isolated_config, (st.st_atime, st.st_mtime + 10))

    with caplog.at_level(logging.WARNING, logger="app.core.mall.product_disambiguator"):
        second = await disamb.disambiguate_product("AirPods 降噪怎么样")
    assert second["status"] == "pass"  # 仍按旧配置（单候选直通）
    assert caplog.records


async def test_invalid_fields_keep_last_valid(monkeypatch, _isolated_config, caplog):
    """字段校验失败（product_id 不符合 P\\d{3}）：沿用旧配置。"""
    _install_ds(monkeypatch)
    bad = {
        "aliases": [
            {"alias": "AirPods", "products": [{"product_id": "PROD4", "symptom_keywords": ["降噪"]}]}
        ]
    }
    import os

    _isolated_config.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    st = _isolated_config.stat()
    os.utime(_isolated_config, (st.st_atime, st.st_mtime + 10))
    with caplog.at_level(logging.WARNING, logger="app.core.mall.product_disambiguator"):
        result = await disamb.disambiguate_product("AirPods 降噪怎么样")
    assert result["status"] == "pass"
    assert caplog.records


async def test_missing_config_file_behaves_as_empty(monkeypatch, tmp_path, caplog):
    """配置文件不存在：等同空配置，所有请求 status=pass，行为与未上线一致。"""
    monkeypatch.setattr(disamb, "_MODELS_PATH", str(tmp_path / "not-exist.json"))
    fake = _install_ds(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="app.core.mall.product_disambiguator"):
        result = await disamb.disambiguate_product("我的 S1 Pro 不吸了")
    assert result["status"] == "pass"
    assert fake.product_calls == []
    assert fake.orders_calls == []


async def test_repo_product_models_config_is_valid(monkeypatch):
    """仓库随附 product_models.json 契约守护：S1 Pro → P006/P007，字段校验通过。

    注意：绕过 autouse fixture 的 tmp 配置——按模块内同款相对规则还原真实路径，
    防止「仓库随附配置非法/路径错误」被 fixture 掩盖（e2e 曾暴露此盲区）。
    """
    import os

    real_path = os.path.normpath(
        os.path.join(os.path.dirname(disamb.__file__), "..", "..", "data", "product_models.json")
    )
    monkeypatch.setattr(disamb, "_MODELS_PATH", real_path)
    disamb._models_cache["mtime"] = None
    disamb._models_cache["data"] = None
    try:
        with open(real_path, encoding="utf-8") as f:
            raw = json.load(f)
        aliases = disamb._validate_models(raw)
        assert aliases is not None
        s1pro = next(a for a in aliases if a["alias"] == "S1 Pro")
        assert {p["product_id"] for p in s1pro["products"]} == {"P006", "P007"}
    finally:
        disamb._models_cache["mtime"] = None
        disamb._models_cache["data"] = None


# ---------- LLM 兜底（可选启用） ----------


async def test_llm_fallback_disabled_by_default_zero_llm_calls(monkeypatch):
    """默认关闭：无信号、订单交集歧义的 query 走完管道 → clarify，全程零 LLM 调用。"""
    _install_ds(monkeypatch)
    mock_call = AsyncMock()
    monkeypatch.setattr(disamb, "call_llm", mock_call)
    result = await disamb.disambiguate_product(
        "我的 S1 Pro 不吸了", requester_user_id="uid-user2", requester_username="user2"
    )
    assert result["status"] == "clarify"
    mock_call.assert_not_awaited()


async def test_llm_fallback_enabled_resolves(monkeypatch):
    """启用且判定成功：call_llm 返回 P007 → resolved、method=llm。"""
    _install_ds(monkeypatch)
    monkeypatch.setattr(disamb, "settings", Settings(DISAMBIG_LLM_FALLBACK=True))
    monkeypatch.setattr(disamb, "call_llm", AsyncMock(return_value='{"product_id": "P007"}'))
    result = await disamb.disambiguate_product(
        "我的 S1 Pro 不吸了", requester_user_id="uid-user2", requester_username="user2"
    )
    assert result["status"] == "resolved"
    assert result["method"] == "llm"
    assert result["product_id"] == "P007"


async def test_llm_fallback_non_candidate_output_treated_as_null(monkeypatch):
    """启用但输出非候选 ID（防幻觉校验）：视为 null，status=clarify，不抛异常。"""
    _install_ds(monkeypatch)
    monkeypatch.setattr(disamb, "settings", Settings(DISAMBIG_LLM_FALLBACK=True))
    monkeypatch.setattr(disamb, "call_llm", AsyncMock(return_value='{"product_id": "P999"}'))
    result = await disamb.disambiguate_product(
        "我的 S1 Pro 不吸了", requester_user_id="uid-user2", requester_username="user2"
    )
    assert result["status"] == "clarify"


async def test_llm_fallback_call_failure_degrades_to_clarify_with_warning(monkeypatch, caplog):
    """启用但调用失败：logger.warning 后 status=clarify，不阻塞主链路。"""
    _install_ds(monkeypatch)
    monkeypatch.setattr(disamb, "settings", Settings(DISAMBIG_LLM_FALLBACK=True))
    monkeypatch.setattr(disamb, "call_llm", AsyncMock(side_effect=RuntimeError("llm down")))
    with caplog.at_level(logging.WARNING, logger="app.core.mall.product_disambiguator"):
        result = await disamb.disambiguate_product(
            "我的 S1 Pro 不吸了", requester_user_id="uid-user2", requester_username="user2"
        )
    assert result["status"] == "clarify"
    assert caplog.records


async def test_llm_fallback_malformed_json_treated_as_null(monkeypatch):
    """启用但输出非 JSON：视为 null，status=clarify。"""
    _install_ds(monkeypatch)
    monkeypatch.setattr(disamb, "settings", Settings(DISAMBIG_LLM_FALLBACK=True))
    monkeypatch.setattr(disamb, "call_llm", AsyncMock(return_value="我觉得是吸奶器"))
    result = await disamb.disambiguate_product(
        "我的 S1 Pro 不吸了", requester_user_id="uid-user2", requester_username="user2"
    )
    assert result["status"] == "clarify"
