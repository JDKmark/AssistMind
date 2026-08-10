"""运维诊断链路 Langfuse 埋点单元测试。

覆盖：
- 启用时 OpsSupervisorAgent().run() → 产生 ops_diagnose 根观察（trace），
  内部嵌套 plan/collect/analyze 观察，report 摘要写入 trace output
- 诊断管线异常 → trace 与对应 span 标记 ERROR，返回降级结构
- 未启用 → 全程不触碰 langfuse 客户端（开关判定均返回 None），返回结构与未埋点一致

mock 策略：
- ops_supervisor 在模块顶部 `from app.core.infra.langfuse import get_langfuse`
  已绑定引用，因此 patch 目标是 app.agents.ops_supervisor.get_langfuse
- LLM 与知识库检索分别 patch ops_supervisor 内的 call_llm / rag_retrieve
  （与 test_ops_supervisor.py 一致），避免真实网络
- force_mock_ops_source（conftest autouse fixture）已把数据源钉为 mock，
  直接 await ds.set_active_scenario("conn_pool_exhausted") 使用预置场景
- 假客户端用 start_as_current_observation 返回假 span（记录 update/end/set_trace_io）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.core.ops import pipeline as ops_supervisor  # patch 目标随流水线迁移
from app.agents.ops_supervisor import OpsSupervisorAgent
from app.core.ops import data_source as ds

_PLAN_JSON = '{"services": ["order-service"], "data_sources": ["metrics", "logs"], "keywords": []}'
_REPORT_JSON = (
    '{"summary": "连接池耗尽", "symptoms": [], "root_cause": "连接池耗尽", '
    '"recovery": "调大连接池", "confidence": 0.9, "affected_services": ["order-service"]}'
)


class _FakeSpan:
    """记录 update/end/set_trace_io 的假 span。"""

    def __init__(self, name: str):
        self.name = name
        self.updates: list[dict[str, Any]] = []
        self.ended = False
        self.trace_io: tuple[Any, Any] | None = None

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)

    def end(self) -> None:
        self.ended = True

    def set_trace_io(self, input=None, output=None) -> None:
        self.trace_io = (input, output)


class _FakeSpanCM:
    """同步上下文管理器：__exit__ 结束 span 且不吞异常。

    模拟 langfuse 4.14 的 _AgnosticContextManager：只有同步协议，
    代码里用同步 with 包住 await（async with 会抛 TypeError）。
    """

    def __init__(self, span: _FakeSpan):
        self.span = span

    def __enter__(self) -> _FakeSpan:
        return self.span

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.span.end()
        return False  # 不吞异常，让异常原样传播


class _FakeLangfuseClient:
    """记录 start_as_current_observation 调用的假 Langfuse 客户端。"""

    def __init__(self):
        self.spans: list[_FakeSpan] = []
        self.observations: list[tuple[str, dict[str, Any]]] = []

    def start_as_current_observation(self, name: str, **kwargs) -> _FakeSpanCM:
        span = _FakeSpan(name)
        self.spans.append(span)
        self.observations.append((name, kwargs))
        return _FakeSpanCM(span)


@pytest.fixture
def fake_langfuse():
    """启用 Langfuse：patch ops_supervisor 内的 get_langfuse 返回假客户端。"""
    client = _FakeLangfuseClient()
    with patch.object(ops_supervisor, "get_langfuse", return_value=client):
        yield client


async def test_run_langfuse_creates_ops_diagnose_trace(fake_langfuse):
    """启用时 run() 产生 ops_diagnose 根观察，嵌套 plan/collect/analyze，report 摘要写入 trace。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    agent = OpsSupervisorAgent()
    with patch(
        "app.core.ops.pipeline.call_llm",
        new=AsyncMock(side_effect=[_PLAN_JSON, _REPORT_JSON]),
    ), patch(
        "app.core.ops.pipeline.rag_retrieve",
        new=AsyncMock(return_value={"contexts": []}),
    ):
        result = await agent.run("订单服务故障")

    # 根观察 ops_diagnose，内部 plan/collect/analyze 各一个（graph 顺序执行，顺序确定）
    names = [s.name for s in fake_langfuse.spans]
    assert names == ["ops_diagnose", "plan", "collect", "analyze"]

    # trace 级 input/output：report 摘要写入（set_trace_io）
    trace_span = fake_langfuse.spans[0]
    assert trace_span.trace_io is not None
    trace_input, trace_output = trace_span.trace_io
    assert trace_input == "订单服务故障"
    assert trace_output["summary"] == "连接池耗尽"
    assert trace_output["root_cause"] == "连接池耗尽"
    assert "degraded" in trace_output

    # plan / collect / analyze span 均写入 output 摘要
    plan_span, collect_span, analyze_span = fake_langfuse.spans[1:]
    assert plan_span.updates[-1]["output"]["services"] == ["order-service"]
    assert "evidence_counts" in collect_span.updates[-1]["output"]
    assert analyze_span.updates[-1]["output"]["summary"] == "连接池耗尽"
    # 所有 span 都已正常结束
    assert all(s.ended for s in fake_langfuse.spans)

    # 返回结构与未埋点一致
    assert set(result) == {"report", "evidence", "plan", "degraded"}
    assert result["report"]["root_cause"] == "连接池耗尽"


async def test_run_langfuse_marks_error_when_pipeline_fails(fake_langfuse):
    """诊断管线异常时 trace 与对应 span 标记 ERROR，返回降级结构。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    agent = OpsSupervisorAgent()
    with patch(
        "app.core.ops.pipeline.call_llm",
        new=AsyncMock(side_effect=[_PLAN_JSON, _REPORT_JSON]),
    ):
        # list_services 在 _plan 的 span 内抛出（在 try/except 之外），
        # 会穿透 _langfuse_span → graph → run() 的异常路径
        with patch(
            "app.core.ops.data_source.list_services",
            new=AsyncMock(side_effect=RuntimeError("mock 数据源异常")),
        ):
            result = await agent.run("订单服务故障")

    # run() 降级结构：degraded 标记 pipeline
    assert result["degraded"] == ["pipeline"]
    assert result["report"]["root_cause"]

    # ops_diagnose trace 与 plan span 均标记 ERROR
    trace_span, plan_span = fake_langfuse.spans[:2]
    assert any(u.get("level") == "ERROR" for u in trace_span.updates)
    assert any(u.get("level") == "ERROR" for u in plan_span.updates)
    assert all(s.ended for s in fake_langfuse.spans)


async def test_run_langfuse_disabled_no_langfuse_touch():
    """未启用时全程不触碰 langfuse 客户端，run() 返回结构与未埋点一致。"""
    await ds.set_active_scenario("conn_pool_exhausted")
    agent = OpsSupervisorAgent()
    # 真实 get_langfuse 依赖模块级 settings，先把启用开关钉为 False，
    # 再用 spy 记录每次开关判定结果（均应为 None = 未启用）
    real_get = ops_supervisor.get_langfuse
    gate_results: list[Any] = []

    def _spy_get():
        v = real_get()
        gate_results.append(v)
        return v

    with patch("app.core.infra.langfuse.is_langfuse_enabled", return_value=False):
        with patch.object(ops_supervisor, "get_langfuse", _spy_get):
            with patch(
                "app.core.ops.pipeline.call_llm",
                new=AsyncMock(side_effect=[_PLAN_JSON, _REPORT_JSON]),
            ):
                with patch(
                    "app.core.ops.pipeline.rag_retrieve",
                    new=AsyncMock(return_value={"contexts": []}),
                ):
                    result = await agent.run("订单服务故障")

    # 返回结构与未埋点一致
    assert set(result) == {"report", "evidence", "plan", "degraded"}
    assert result["report"]["root_cause"] == "连接池耗尽"
    # 每次开关判定均为未启用（None）→ 客户端从未被触碰，无任何观察创建
    assert gate_results and all(v is None for v in gate_results)
