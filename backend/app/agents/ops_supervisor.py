"""OpsSupervisorAgent：运维诊断编排（Orchestrator-Workers 模式）。

编排链路（LangGraph StateGraph）：
1. supervisor 节点：LLM 决策需要哪些数据源（指标/日志/变更/知识库）+ 关注服务
2. collect 节点：并行 Worker 采集证据（asyncio.gather）
3. analyze 节点：LLM 综合证据 → 根因 + 恢复建议（结构化报告）

失败降级：
- supervisor LLM 决策失败 → 全量采集（所有数据源）
- analyze LLM 失败 → 基于告警/变更的规则化报告
- 单个数据源失败 → 其余数据源不受影响（返回降级标记）
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator, TypedDict

from langgraph.graph import END, StateGraph

from app.core.infra.langfuse import get_langfuse
from app.core.infra.llm_factory import LLMUnavailableError, call_llm
from app.core.ops import data_source as ops_ds
from app.core.rag.engine import retrieve as rag_retrieve
from app.core.ticket_service import list_tickets, search_tickets

logger = logging.getLogger(__name__)

# 指标全景：用于全局对比（所有服务）
_PANORAMA_METRICS = ["error_rate", "latency_p95", "cpu_usage", "memory_usage"]
# 详细指标（命中异常后细查）
_DETAIL_METRICS = ["error_rate", "latency_p95", "qps"]


@contextmanager
def _langfuse_span(name: str, **kwargs: Any) -> Iterator[Any | None]:
    """开启一个 Langfuse 观察（span/trace），未启用时 yield None（全 no-op）。

    langfuse 4.14.2 关键结论（依据 venv site-packages/langfuse/_client/{client,span}.py）：
    - start_as_current_observation() 返回 _AgnosticContextManager（继承
      contextlib._GeneratorContextManager，无 __aenter__），**async with 会报 TypeError**，
      async 环境用同步 `with` 即可：OTEL 上下文基于 contextvars，await 期间同一 task 内
      "当前 span" 保持不变（asyncio.gather 创建子任务时会复制 contextvars，
      Worker 产生的嵌套观察仍会挂到 collect span 下）。
    - 无父 span 时创建的根观察即一条新 trace（Langfuse 服务端按根观察归并 trace，
      trace 名取根观察名）；嵌套观察（包括 call_llm 里用 start_as_current_observation /
      @observe 创建的 span）通过当前 context 自动挂到本 span 下，无需显式传 trace_id。
    - span.update(output=..., metadata=..., level=..., status_message=...) 可在存活期补数据；
      span.end() 在 with 退出时自动调用；异常时标记 ERROR 后原样抛出（不改变调用方异常处理）。
    """
    langfuse = get_langfuse()
    if langfuse is None:
        yield None
        return
    with langfuse.start_as_current_observation(name=name, **kwargs) as span:
        try:
            yield span
        except Exception as e:
            span.update(level="ERROR", status_message=str(e)[:2000])
            raise


class OpsState(TypedDict, total=False):
    """Supervisor 编排状态。"""

    query: str
    plan: dict[str, Any]          # supervisor 决策：{services, data_sources, keywords}
    evidence: dict[str, Any]      # collect 采集的证据
    report: dict[str, Any]        # analyze 输出的诊断报告
    degraded: list[str]           # 降级项


_PLAN_SYSTEM = (
    "你是运维诊断规划器。根据用户描述的故障现象，决定诊断需要采集哪些数据源。\n"
    "可选数据源：metrics(监控指标)、logs(日志)、changes(变更记录)、kb(故障案例知识库)。\n"
    "输出 JSON，格式："
    '{"services": ["服务名列表，最多3个"], "data_sources": ["metrics","logs","changes","kb"], "keywords": ["日志搜索关键词"]}\n'
    "服务名取值：api-gateway、order-service、inventory-service、payment-service、user-service。"
)


def _build_plan_prompt(query: str) -> str:
    return f"用户描述：{query}\n请输出诊断计划 JSON："


def _parse_plan(resp: str) -> dict[str, Any]:
    """解析 supervisor 决策 JSON。失败返回空 dict（调用方降级）。"""
    raw = resp.strip() if isinstance(resp, str) else str(resp)
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            services = [s for s in obj.get("services", []) if isinstance(s, str)]
            sources = [s for s in obj.get("data_sources", []) if isinstance(s, str)]
            keywords = [k for k in obj.get("keywords", []) if isinstance(k, str)]
            return {"services": services, "data_sources": sources, "keywords": keywords}
        except json.JSONDecodeError as e:
            logger.warning("[OpsSupervisor] 计划 JSON 解析失败: %s, raw=%r", e, raw)
    logger.warning("[OpsSupervisor] 计划解析失败，将降级全量采集: %r", raw)
    return {}


async def _plan(query: str) -> dict[str, Any]:
    """LLM 决策诊断计划。失败降级为全量采集。"""
    # Langfuse：plan span（输出为最终决策计划）
    with _langfuse_span("plan", input=query) as span:
        try:
            resp = await call_llm(_build_plan_prompt(query), system=_PLAN_SYSTEM)
            plan = _parse_plan(resp)
        except LLMUnavailableError as e:
            logger.warning("[OpsSupervisor] LLM 不可用，降级全量采集: %s", e)
            plan = {}
        except Exception as e:
            logger.warning("[OpsSupervisor] 计划决策异常，降级全量采集: %s", e)
            plan = {}

        services = [s for s in plan.get("services", []) if s in await ops_ds.list_services()]
        if not services:
            services = list(await ops_ds.list_services())
        sources = plan.get("data_sources") or ["metrics", "logs", "changes", "kb"]
        valid_sources = [s for s in sources if s in ("metrics", "logs", "changes", "kb")]
        if not valid_sources:
            valid_sources = ["metrics", "logs", "changes", "kb"]
        result = {
            "services": services,
            "data_sources": valid_sources,
            "keywords": plan.get("keywords", []) or [],
        }
        if span is not None:
            span.update(output=result)
        return result


async def _collect_metrics(services: list[str]) -> list[dict[str, Any]]:
    """采集指标：先全景对比，异常服务细查。"""
    # 数据源已 async 化，直接并发调用
    tasks = [
        ops_ds.query_metric(svc, m)
        for svc in services
        for m in _PANORAMA_METRICS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    metrics: list[dict[str, Any]] = []
    abnormal_services: set[str] = set()
    idx = 0
    for svc in services:
        for m in _PANORAMA_METRICS:
            pts = results[idx]
            idx += 1
            if isinstance(pts, Exception) or not pts:
                continue
            values = [p["value"] for p in pts]
            summary = {
                "current": values[-1],
                "max": round(max(values), 2),
                "min": round(min(values), 2),
                "avg": round(sum(values) / len(values), 2),
            }
            metrics.append({"service": svc, "metric": m, "summary": summary})
            # 判定异常：错误率>1% 或 延迟>500ms 或 CPU/内存>80%
            baseline = {
                "api-gateway": {"error_rate": 0.1, "latency_p95": 120, "cpu_usage": 35, "memory_usage": 55},
                "order-service": {"error_rate": 0.2, "latency_p95": 200, "cpu_usage": 40, "memory_usage": 60},
                "inventory-service": {"error_rate": 0.1, "latency_p95": 80, "cpu_usage": 30, "memory_usage": 50},
                "payment-service": {"error_rate": 0.1, "latency_p95": 150, "cpu_usage": 45, "memory_usage": 65},
                "user-service": {"error_rate": 0.05, "latency_p95": 60, "cpu_usage": 25, "memory_usage": 45},
            }
            bl = baseline.get(svc, {})
            if m == "error_rate" and summary["max"] > bl.get("error_rate", 0.2) * 5:
                abnormal_services.add(svc)
            elif m == "latency_p95" and summary["max"] > bl.get("latency_p95", 100) * 3:
                abnormal_services.add(svc)
            elif m in ("cpu_usage", "memory_usage") and summary["max"] > 80:
                abnormal_services.add(svc)

    # 异常服务细查（error_rate/latency/qps 趋势）
    detail_tasks = []
    for svc in abnormal_services:
        for m in _DETAIL_METRICS:
            detail_tasks.append((svc, m))
    detail_results = await asyncio.gather(
        *[ops_ds.query_metric(s, m) for s, m in detail_tasks],
        return_exceptions=True,
    )
    for (svc, m), pts in zip(detail_tasks, detail_results):
        if isinstance(pts, Exception) or not pts:
            continue
        values = [p["value"] for p in pts]
        metrics.append(
            {
                "service": svc,
                "metric": m,
                "summary": {
                    "current": values[-1],
                    "max": round(max(values), 2),
                    "min": round(min(values), 2),
                    "avg": round(sum(values) / len(values), 2),
                },
                "detail": True,
            }
        )
    return metrics


async def collect(plan: dict[str, Any], query: str) -> dict[str, Any]:
    """并行 Worker 采集证据。"""
    # Langfuse：collect span（输出为各数据源条目数与 degraded 列表）
    with _langfuse_span("collect", input={"query": query, "plan": plan}) as span:
        services = plan.get("services") or list(await ops_ds.list_services())
        sources = plan.get("data_sources") or []
        keywords = plan.get("keywords") or []
        degraded: list[str] = []

        evidence: dict[str, Any] = {
            "metrics": [], "logs": [], "changes": [], "kb": [], "alerts": [],
            "tickets": [], "hosts": {},
        }

        try:
            evidence["alerts"] = await ops_ds.get_alerts()
        except Exception as e:
            logger.warning("[OpsSupervisor] 告警采集失败: %s", e)
            degraded.append("alerts")

        # 历史工单相似检索（"过去是否出现过相似故障"）：有关键词用模糊匹配，
        # 无关键词展示最近工单；PostgreSQL 不可用降级为空 + degraded，不阻断诊断
        try:
            if keywords:
                evidence["tickets"] = await search_tickets(keywords[0], limit=5)
            else:
                recent = await list_tickets(limit=5)
                evidence["tickets"] = recent.get("tickets", [])
        except Exception as e:
            logger.warning("[OpsSupervisor] 历史工单检索失败: %s", e)
            degraded.append("tickets")

        # 部署拓扑（CMDB）：受影响服务的主机/实例清单
        try:
            for svc in services:
                hosts = await ops_ds.list_hosts(svc)
                if hosts:
                    evidence["hosts"][svc] = hosts
        except Exception as e:
            logger.warning("[OpsSupervisor] 主机拓扑采集失败: %s", e)
            degraded.append("hosts")

        if "metrics" in sources:
            evidence["metrics"] = await _collect_metrics(services)

        if "logs" in sources:
            try:
                logs = await ops_ds.search_logs(keyword=keywords[0] if keywords else None, limit=30)
                if not logs:
                    logs = await ops_ds.search_logs(limit=15)
                evidence["logs"] = logs
            except Exception as e:
                logger.warning("[OpsSupervisor] 日志采集失败: %s", e)
                degraded.append("logs")

        if "changes" in sources:
            try:
                evidence["changes"] = await ops_ds.query_changes(limit=10)
            except Exception as e:
                logger.warning("[OpsSupervisor] 变更采集失败: %s", e)
                degraded.append("changes")

        if "kb" in sources:
            try:
                kb = await rag_retrieve(query, role="agent")
                evidence["kb"] = [
                    {
                        "doc_id": c.get("doc_id", ""),
                        "title": c.get("title", ""),
                        "score": round(c.get("score", 0), 3),
                        "text": c.get("text", "")[:200],
                    }
                    for c in kb.get("contexts", [])[:3]
                ]
                if not evidence["kb"]:
                    degraded.append("kb")
            except Exception as e:
                logger.warning("[OpsSupervisor] 知识库检索失败: %s", e)
                degraded.append("kb")

        if span is not None:
            span.update(
                output={
                    "evidence_counts": {
                        "alerts": len(evidence["alerts"]),
                        "metrics": len(evidence["metrics"]),
                        "logs": len(evidence["logs"]),
                        "changes": len(evidence["changes"]),
                        "kb": len(evidence["kb"]),
                        "tickets": len(evidence["tickets"]),
                        "hosts": len(evidence["hosts"]),
                    },
                    "degraded": degraded,
                }
            )
        return {"evidence": evidence, "degraded": degraded}


_ANALYZE_SYSTEM = (
    "你是资深 SRE 运维诊断专家。根据采集的监控证据，输出结构化诊断报告。\n"
    "输出 JSON，格式："
    '{"summary": "一句话结论", "symptoms": ["症状列表"], '
    '"root_cause": "根因分析", "recovery": "恢复建议", '
    '"confidence": 0.0到1.0的数字, "affected_services": ["受影响服务"]}\n'
    "要求：根因要有证据支撑，不要编造；恢复建议要可执行。"
)


def _build_analyze_prompt(query: str, evidence: dict[str, Any]) -> str:
    lines = [f"用户描述：{query}", ""]

    alerts = evidence.get("alerts", [])
    if alerts:
        lines.append("【告警】")
        lines.extend(f"- [{a['severity']}] {a['service']} {a['metric']}: {a['message']}" for a in alerts)

    metrics = evidence.get("metrics", [])
    if metrics:
        lines.append("【指标】")
        for m in metrics:
            s = m["summary"]
            tag = "异常" if m.get("detail") else ""
            lines.append(
                f"- {m['service']} {m['metric']}{tag}: current={s['current']} max={s['max']} avg={s['avg']}"
            )

    logs = evidence.get("logs", [])
    if logs:
        lines.append("【日志】")
        for lg in logs[:10]:
            lines.append(f"- [{lg['level']}] {lg['service']}: {lg['message'][:120]}")

    changes = evidence.get("changes", [])
    if changes:
        lines.append("【变更】")
        for c in changes[:5]:
            lines.append(f"- {c['service']} ({c['type']}): {c['content'][:100]}")

    kb = evidence.get("kb", [])
    if kb:
        lines.append("【知识库案例】")
        for k in kb[:3]:
            lines.append(f"- {k['title']}: {k['text'][:120]}")

    tickets = evidence.get("tickets", [])
    if tickets:
        lines.append("【相似历史工单】")
        for t in tickets[:3]:
            status = t.get("status", "")
            lines.append(f"- [{t.get('id', '')}] {t.get('title', '')[:80]}（{status}）")

    # 控制 prompt 长度
    return "\n".join(lines)[:4000]


def _parse_report(resp: str) -> dict[str, Any]:
    raw = resp.strip() if isinstance(resp, str) else str(resp)
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            return {
                "summary": str(obj.get("summary", "")),
                "symptoms": obj.get("symptoms", []) if isinstance(obj.get("symptoms"), list) else [],
                "root_cause": str(obj.get("root_cause", "")),
                "recovery": str(obj.get("recovery", "")),
                "confidence": float(obj.get("confidence", 0.5)),
                "affected_services": obj.get("affected_services", [])
                if isinstance(obj.get("affected_services"), list)
                else [],
            }
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("[OpsSupervisor] 报告 JSON 解析失败: %s", e)
    # 兜底：从证据生成规则报告
    return _fallback_report(raw)


def _fallback_report(raw: str = "") -> dict[str, Any]:
    """规则化兜底报告：基于告警与变更记录推断。"""
    return {
        "summary": "已采集证据但 LLM 综合失败，以下为基于告警与变更的规则化结论。",
        "symptoms": [],
        "root_cause": raw[:200] if raw else "无法自动定位根因，请结合告警与变更记录人工判断。",
        "recovery": "建议先回滚/暂停最近一次变更，观察指标恢复情况；必要时升级 SRE 人工介入。",
        "confidence": 0.3,
        "affected_services": [],
    }


async def analyze(query: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """LLM 综合证据输出诊断报告。失败降级为规则报告。"""
    # Langfuse：analyze span（输入只带各数据源条目数，避免把完整证据上传；
    # 输出为 report 摘要）
    evidence_counts = {
        k: len(v) for k, v in evidence.items() if isinstance(v, list)
    }
    with _langfuse_span("analyze", input={"query": query, "evidence_counts": evidence_counts}) as span:
        prompt = _build_analyze_prompt(query, evidence)
        try:
            resp = await call_llm(prompt, system=_ANALYZE_SYSTEM)
            report = _parse_report(resp)
        except LLMUnavailableError as e:
            logger.warning("[OpsSupervisor] LLM 不可用，输出规则报告: %s", e)
            report = _fallback_report()
        except Exception as e:
            logger.warning("[OpsSupervisor] 分析异常，输出规则报告: %s", e)
            report = _fallback_report()

        # 补充证据摘要供前端展示
        report["evidence_summary"] = {
            "alerts": len(evidence.get("alerts", [])),
            "metrics": len(evidence.get("metrics", [])),
            "logs": len(evidence.get("logs", [])),
            "changes": len(evidence.get("changes", [])),
            "kb": len(evidence.get("kb", [])),
        }
        if span is not None:
            span.update(
                output={
                    "summary": report.get("summary"),
                    "root_cause": report.get("root_cause"),
                    "confidence": report.get("confidence"),
                    "affected_services": report.get("affected_services"),
                    "recovery": report.get("recovery"),
                }
            )
        return report


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
