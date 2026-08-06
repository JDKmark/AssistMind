"""OpsSupervisorAgent 单元测试。

覆盖：
- _plan：LLM 决策解析 / LLM 失败降级全量
- collect：并行采集含指标/日志/变更/告警、单源失败降级
- analyze：LLM 成功报告 / LLM 失败规则报告
- run：完整编排
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.agents import ops_supervisor
from app.agents.ops_supervisor import OpsSupervisorAgent
from app.core.infra.llm_factory import LLMUnavailableError
from app.core.ops import data_source as ds


async def test_plan_parse_success():
    """LLM 返回合法 JSON 时解析计划。"""
    with patch(
        "app.agents.ops_supervisor.call_llm",
        new=AsyncMock(
            return_value=(
                '{"services": ["order-service", "inventory-service"], '
                '"data_sources": ["metrics", "logs", "changes"], "keywords": ["connection"]}'
            )
        ),
    ):
        plan = await ops_supervisor._plan("订单服务故障")
    assert plan["services"] == ["order-service", "inventory-service"]
    assert "metrics" in plan["data_sources"]
    assert plan["keywords"] == ["connection"]


async def test_plan_fallback_when_llm_unavailable():
    """LLM 不可用时降级为全量采集。"""
    with patch(
        "app.agents.ops_supervisor.call_llm",
        new=AsyncMock(side_effect=LLMUnavailableError("unavailable")),
    ):
        plan = await ops_supervisor._plan("订单服务故障")
    assert set(plan["data_sources"]) == {"metrics", "logs", "changes", "kb"}
    assert len(plan["services"]) >= 1


async def test_plan_fallback_when_invalid_services():
    """LLM 返回非法服务名时回退为全部服务。"""
    with patch(
        "app.agents.ops_supervisor.call_llm",
        new=AsyncMock(
            return_value='{"services": ["bad-service"], "data_sources": ["metrics"]}'
        ),
    ):
        plan = await ops_supervisor._plan("x")
    assert "bad-service" not in plan["services"]
    assert len(plan["services"]) > 0


async def test_collect_gathers_evidence():
    """collect 采集指标/日志/变更/告警。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    plan = {"services": ["order-service", "inventory-service"], "data_sources": ["metrics", "logs", "changes", "kb"], "keywords": []}
    with patch(
        "app.agents.ops_supervisor.rag_retrieve",
        new=AsyncMock(return_value={"contexts": [{"doc_id": "ops-1", "title": "t", "text": "x"}]}),
    ):
        result = await ops_supervisor.collect(plan, "订单服务故障")
    evidence = result["evidence"]
    assert len(evidence["metrics"]) > 0
    assert len(evidence["logs"]) > 0
    assert len(evidence["changes"]) > 0
    assert len(evidence["alerts"]) > 0
    assert len(evidence["kb"]) == 1


async def test_collect_metrics_marks_abnormal_detail():
    """异常服务应触发 detail 指标细查。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    metrics = await ops_supervisor._collect_metrics(["order-service", "inventory-service"])
    detail = [m for m in metrics if m.get("detail")]
    assert len(detail) >= 1


async def test_collect_kb_failure_degraded():
    """知识库检索失败时降级标记 kb，不影响其他数据源。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    plan = {"services": ["order-service"], "data_sources": ["metrics", "logs", "kb"], "keywords": []}
    with patch(
        "app.agents.ops_supervisor.rag_retrieve",
        new=AsyncMock(side_effect=RuntimeError("qdrant down")),
    ):
        result = await ops_supervisor.collect(plan, "x")
    assert "kb" in result["degraded"]
    assert len(result["evidence"]["metrics"]) > 0


async def test_collect_tickets_and_hosts():
    """历史工单与主机拓扑进入证据，失败时标记 degraded。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    plan = {"services": ["order-service"], "data_sources": ["metrics"], "keywords": ["connection"]}
    with patch(
        "app.agents.ops_supervisor.search_tickets",
        new=AsyncMock(
            return_value=[
                {"id": "TK-OLD1", "title": "连接池耗尽故障", "status": "resolved", "category": "incident"}
            ]
        ),
    ):
        result = await ops_supervisor.collect(plan, "x")
    assert result["evidence"]["tickets"][0]["id"] == "TK-OLD1"
    assert "order-service" in result["evidence"]["hosts"]
    assert "hosts" not in result["degraded"]


async def test_collect_ticket_query_failure_degraded():
    """工单检索失败降级为空 + degraded，不阻断其他证据。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    plan = {"services": ["order-service"], "data_sources": ["metrics"], "keywords": ["connection"]}
    with patch(
        "app.agents.ops_supervisor.search_tickets",
        new=AsyncMock(side_effect=RuntimeError("postgres down")),
    ):
        result = await ops_supervisor.collect(plan, "x")
    assert "tickets" in result["degraded"]
    assert len(result["evidence"]["metrics"]) > 0


async def test_analyze_success():
    """LLM 返回合法 JSON 时生成报告。"""
    evidence = {"alerts": [], "metrics": [], "logs": [], "changes": [], "kb": []}
    with patch(
        "app.agents.ops_supervisor.call_llm",
        new=AsyncMock(
            return_value=(
                '{"summary": "连接池耗尽", "symptoms": ["错误率上升"], '
                '"root_cause": "连接池过小", "recovery": "调大连接池", '
                '"confidence": 0.9, "affected_services": ["order-service"]}'
            )
        ),
    ):
        report = await ops_supervisor.analyze("x", evidence)
    assert report["root_cause"] == "连接池过小"
    assert report["confidence"] == 0.9
    assert report["evidence_summary"]["alerts"] == 0


async def test_analyze_fallback_when_llm_unavailable():
    """LLM 不可用时输出规则报告（不抛异常）。"""
    evidence = {"alerts": [], "metrics": [], "logs": [], "changes": [], "kb": []}
    with patch(
        "app.agents.ops_supervisor.call_llm",
        new=AsyncMock(side_effect=LLMUnavailableError("down")),
    ):
        report = await ops_supervisor.analyze("x", evidence)
    assert report["root_cause"]
    assert report["confidence"] < 0.5


async def test_analyze_fallback_on_bad_json():
    """LLM 返回非 JSON 时降级规则报告。"""
    evidence = {"alerts": [], "metrics": [], "logs": [], "changes": [], "kb": []}
    with patch(
        "app.agents.ops_supervisor.call_llm",
        new=AsyncMock(return_value="完全不是 JSON 的内容"),
    ):
        report = await ops_supervisor.analyze("x", evidence)
    assert report["root_cause"]


async def test_run_end_to_end():
    """完整编排：supervisor → collect → analyze 返回报告。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    agent = OpsSupervisorAgent()
    with patch(
        "app.agents.ops_supervisor.call_llm",
        new=AsyncMock(
            side_effect=[
                '{"services": ["order-service"], "data_sources": ["metrics", "logs"], "keywords": []}',
                '{"summary": "s", "symptoms": [], "root_cause": "连接池耗尽", "recovery": "r", "confidence": 0.9, "affected_services": ["order-service"]}',
            ]
        ),
    ):
        with patch(
            "app.agents.ops_supervisor.rag_retrieve",
            new=AsyncMock(return_value={"contexts": []}),
        ):
            result = await agent.run("订单服务故障")
    assert result["report"]["root_cause"] == "连接池耗尽"
    assert result["plan"]["services"] == ["order-service"]
