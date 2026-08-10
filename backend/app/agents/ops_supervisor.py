"""OpsSupervisorAgent：运维诊断编排（Orchestrator-Workers 模式）。

编排链路（LangGraph StateGraph）：
1. supervisor 节点：LLM 决策需要哪些数据源（指标/日志/变更/知识库）+ 关注服务
2. collect 节点：并行 Worker 采集证据（asyncio.gather）
3. analyze 节点：LLM 综合证据 → 根因 + 恢复建议（结构化报告）

业务流水线（计划/采集/分析）实现在 `app.core.ops.pipeline`——Agent 编排与
API 层 SSE 流式共用同一套函数；本模块只保留 LangGraph 节点与状态组装。

兼容导出：pipeline 的流程函数（_plan/collect/analyze/_langfuse_span/OpsState）
在此 re-export，供 API 层与既有测试引用。
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from app.core.ops.pipeline import (
    _langfuse_span,
    _plan,
    OpsState,
    analyze,
    collect,
)

logger = logging.getLogger(__name__)

__all__ = ["OpsSupervisorAgent", "OpsState", "_plan", "collect", "analyze", "_langfuse_span"]


class OpsSupervisorAgent:
    """运维诊断 Supervisor Agent（Orchestrator-Workers）。"""

    async def supervisor_node(self, state: OpsState) -> dict:
        """决策节点：确定采集范围。"""
        plan = await _plan(state.get("query", ""))
        logger.info("[OpsSupervisor] 诊断计划: services=%s sources=%s", plan["services"], plan["data_sources"])
        return {"plan": plan}

    async def collect_node(self, state: OpsState) -> dict:
        """并行采集节点：Worker 采集证据。"""
        result = await collect(state.get("plan", {}), state.get("query", ""))
        return {"evidence": result["evidence"], "degraded": state.get("degraded", []) + result["degraded"]}

    async def analyze_node(self, state: OpsState) -> dict:
        """综合分析节点：输出诊断报告。"""
        report = await analyze(state.get("query", ""), state.get("evidence", {}))
        return {"report": report}

    def build_graph(self):
        graph = StateGraph(OpsState)
        graph.add_node("supervisor", self.supervisor_node)
        graph.add_node("collect", self.collect_node)
        graph.add_node("analyze", self.analyze_node)
        graph.set_entry_point("supervisor")
        graph.add_edge("supervisor", "collect")
        graph.add_edge("collect", "analyze")
        graph.add_edge("analyze", END)
        return graph.compile()

    async def run(self, query: str) -> dict[str, Any]:
        """执行一次诊断。返回 {report, evidence, plan, degraded}。"""
        initial: OpsState = {
            "query": query,
            "plan": {},
            "evidence": {},
            "report": {},
            "degraded": [],
        }
        # Langfuse：创建 ops_diagnose trace（根观察即一条新 trace），graph 节点内
        # _plan/collect/analyze 的 span 通过 OTEL context 自动嵌套进来；
        # 异常路径把错误记进 trace 并正常 end（返回结构不变）。
        # set_trace_io() 在 4.14 已标记 deprecated（运行时仅 DeprecationWarning，
        # 不影响功能），是当前 SDK 中设置 trace 级 input/output 的唯一入口。
        with _langfuse_span("ops_diagnose", input=query) as trace_span:
            try:
                graph = self.build_graph()
                final = await graph.ainvoke(initial, config={"recursion_limit": 10})
                result = {
                    "report": final.get("report", {}),
                    "evidence": final.get("evidence", {}),
                    "plan": final.get("plan", {}),
                    "degraded": final.get("degraded", []),
                }
            except Exception as e:
                logger.warning("[OpsSupervisor] 诊断执行异常: %s", e)
                if trace_span is not None:
                    trace_span.update(level="ERROR", status_message=str(e)[:2000])
                result = {
                    "report": {
                        "summary": "诊断流程执行异常，请重试。",
                        "symptoms": [],
                        "root_cause": str(e),
                        "recovery": "请稍后重试或联系 SRE。",
                        "confidence": 0.0,
                        "affected_services": [],
                    },
                    "evidence": {},
                    "plan": {},
                    "degraded": ["pipeline"],
                }
            if trace_span is not None:
                report = result["report"]
                trace_span.set_trace_io(
                    input=query,
                    output={
                        "summary": report.get("summary"),
                        "root_cause": report.get("root_cause"),
                        "confidence": report.get("confidence"),
                        "affected_services": report.get("affected_services"),
                        "degraded": result.get("degraded", []),
                        "ticket_id": report.get("ticket_id"),
                    },
                )
            return result
