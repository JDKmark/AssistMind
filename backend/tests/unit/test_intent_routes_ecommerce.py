"""电商语境意图路由单元测试（phase7 Task 4：客服链路配置）。

覆盖：
- 新增电商关键词经 intent.route 规则层命中对应意图（task / faq / chat）
- intent_routes.json 结构不变（4 类意图、keywords/samples 数组）且电商样本就位
- 运维入口下架后"下单失败"类 query 不再路由到 diagnose

断言方式：直接调用实际解析函数 intent.route()，mock 掉语义/LLM 依赖；
规则层命中时语义与 LLM 均不应被调用（短路验证）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.router import intent, semantic

# 规则层短路验证：规则命中后不应再调用 LLM
_LLM_SHOULD_NOT_BE_CALLED = AsyncMock(
    side_effect=AssertionError("规则命中不应调用 LLM")
)


@pytest.fixture(autouse=True)
def _reset_router_caches():
    """每个测试前后重置路由模块级缓存，保证隔离。"""
    semantic.reset_cache()
    intent._routes_cache["mtime"] = None
    intent._routes_cache["data"] = None
    yield
    semantic.reset_cache()
    intent._routes_cache["mtime"] = None
    intent._routes_cache["data"] = None


async def _route_rule_only(query: str) -> dict:
    """仅走规则层调用实际路由：语义与 LLM 均 mock 为不应被调用。"""
    with (
        patch("app.core.router.semantic.embed_one", new=AsyncMock(return_value=None)),
        patch("app.core.router.intent.call_llm", new=_LLM_SHOULD_NOT_BE_CALLED),
    ):
        return await intent.route(query)


async def test_ecommerce_rule_route_return_task():
    """电商关键词"退货"命中 task："我要退货"→task。"""
    result = await _route_rule_only("我要退货")

    assert result["intent"] == "task"
    assert result["source"] == "rule"
    assert result["confidence"] == 1.0
    assert result["low_confidence"] is False


async def test_ecommerce_rule_route_shipping_faq():
    """电商关键词"运费"命中 faq："运费谁出"→faq。"""
    result = await _route_rule_only("运费谁出")

    assert result["intent"] == "faq"
    assert result["source"] == "rule"
    assert result["confidence"] == 1.0


async def test_ecommerce_order_fail_not_routed_to_diagnose():
    """运维入口下架后"我下单失败了"不再路由到 diagnose。

    diagnose 关键词已从 intent_routes.json 移除，规则层不再命中；
    语义层不可用时由 LLM 兜底，落入 task / unclear 均可接受，
    只需保证不再进入已下架的 diagnose 意图。
    """
    with (
        patch("app.core.router.semantic.embed_one", new=AsyncMock(return_value=None)),
        patch(
            "app.core.router.intent.call_llm",
            new=AsyncMock(return_value='{"intent": "task", "confidence": 0.9}'),
        ),
    ):
        result = await intent.route("我下单失败了")

    assert result["intent"] != "diagnose"


async def test_ecommerce_rule_route_greeting_chat():
    """chat 保留："你好"→chat。"""
    result = await _route_rule_only("你好")

    assert result["intent"] == "chat"
    assert result["source"] == "rule"
    assert result["confidence"] == 1.0


def test_intent_routes_structure_and_ecommerce_samples():
    """intent_routes.json 结构不变，电商关键词与语义样本就位。"""
    routes = intent._load_routes()

    # 结构：4 类意图，每类均为 keywords / samples 数组
    assert set(routes.keys()) == {"faq", "task", "chat", "unclear"}
    for cfg in routes.values():
        assert isinstance(cfg["keywords"], list)
        assert isinstance(cfg["samples"], list)
        assert len(cfg["keywords"]) == len(set(cfg["keywords"]))
        assert len(cfg["samples"]) == len(set(cfg["samples"]))

    # faq 电商关键词
    for kw in [
        "运费",
        "发货时效",
        "优惠券",
        "满减",
        "会员价",
        "积分",
        "退货政策",
        "售后",
        "包邮",
        "服务承诺",
        "秒杀",
    ]:
        assert kw in routes["faq"]["keywords"], f"faq 缺少关键词: {kw}"

    # task 电商关键词
    for kw in [
        "查订单",
        "查物流",
        "退款",
        "退货",
        "申请售后",
        "订单状态",
        "物流到哪了",
        "买的东西",
    ]:
        assert kw in routes["task"]["keywords"], f"task 缺少关键词: {kw}"

    # 语义样本就位
    for sample in ["运费谁出", "优惠券能叠加吗", "发货要多久能到", "退货政策是什么"]:
        assert sample in routes["faq"]["samples"], f"faq 缺少样本: {sample}"
    for sample in ["我要退货", "查一下我的订单", "帮我查一下物流到哪了", "我要申请售后"]:
        assert sample in routes["task"]["samples"], f"task 缺少样本: {sample}"
