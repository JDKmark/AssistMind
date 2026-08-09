"""运维诊断 API。

- POST /api/v1/ops/diagnose：SSE 流式诊断（start→planning→collecting→analyzing→incident→done）
- POST /api/v1/ops/scenario：设置活动故障场景（演示用）
- GET  /api/v1/ops/scenarios：列出预置场景
- GET  /api/v1/ops/services：列出服务
- GET  /api/v1/ops/metrics/{service}/{metric}：查询指标时序
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.ops_supervisor import _langfuse_span, _plan, analyze, collect
from app.api.deps import get_current_user
from app.core.infra.langfuse import get_langfuse
from app.core.ops import data_source as ops_ds
from app.core.ticket_service import create_ticket as _create_ticket

logger = logging.getLogger(__name__)

router = APIRouter()

_SSE_MEDIA_TYPE = "text/event-stream"


class DiagnoseRequest(BaseModel):
    """诊断请求体。"""

    query: str = Field(..., min_length=1, description="故障描述")
    create_incident: bool = Field(True, description="诊断完成后是否自动创建故障工单")


class ScenarioRequest(BaseModel):
    """设置活动场景。name=None 表示恢复无故障基线。"""

    name: str | None = None


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _emit_sse_stage(stage: str) -> None:
    """在 Langfuse trace 中补记一个 SSE 阶段事件（未启用时 no-op）。

    create_event 是即时观察（创建即结束），自动挂在当前 span 下；
    metadata 记录阶段名与时间戳，便于在 trace 时间线上核对 SSE 各阶段。
    """
    langfuse = get_langfuse()
    if langfuse is None:
        return
    langfuse.create_event(
        name=f"sse_{stage}",
        metadata={"stage": stage, "ts": round(time.time(), 3)},
    )


async def _diagnose_stream(
    req: DiagnoseRequest, include_start: bool = True
) -> AsyncIterator[str]:
    """SSE 诊断事件流。

    include_start=False 用于 chat 接口（chat 已发送 start 事件），避免重复。

    Langfuse 埋点：整条流包在 ops_diagnose trace 里（与 ops_supervisor.run 同构），
    _plan/collect/analyze 的 span 通过 OTEL context 自动嵌套进本 trace；
    每个 SSE 阶段 create_event 补记阶段名与时间戳；
    done 时用 set_trace_io 把 report 摘要 / degraded / ticket_id 写入 trace，
    error 时给 trace 标记 ERROR。未启用时 trace_span 为 None，SSE 行为完全不变。
    """
    with _langfuse_span("ops_diagnose", input=req.query) as trace_span:
        try:
            if include_start:
                _emit_sse_stage("start")
                yield _sse("start", {"query": req.query})

            # 1. supervisor 决策
            plan = await _plan(req.query)
            _emit_sse_stage("planning")
            yield _sse("planning", {"plan": plan})

            # 2. 并行采集
            _emit_sse_stage("collecting")
            yield _sse("collecting", {})
            coll = await collect(plan, req.query)
            evidence = coll["evidence"]
            _emit_sse_stage("evidence")
            yield _sse(
                "evidence",
                {
                    "alerts": evidence.get("alerts", []),
                    "metrics": evidence.get("metrics", []),
                    "logs": evidence.get("logs", [])[:10],
                    "changes": evidence.get("changes", []),
                    "kb": evidence.get("kb", []),
                    "tickets": evidence.get("tickets", [])[:5],
                    "hosts": evidence.get("hosts", {}),
                },
            )

            # 3. 综合分析
            _emit_sse_stage("analyzing")
            yield _sse("analyzing", {})
            report = await analyze(req.query, evidence)

            # 4. 自动创建故障工单（闭环）
            ticket = None
            if req.create_incident and report.get("root_cause"):
                try:
                    ticket = await _create_ticket(
                        title=f"[故障] {report.get('summary', '')[:80]}",
                        description=(
                            f"症状: {json.dumps(report.get('symptoms', []), ensure_ascii=False)}\n"
                            f"根因: {report.get('root_cause', '')}\n"
                            f"建议: {report.get('recovery', '')}"
                        ),
                        priority="high",
                        category="incident",
                    )
                    report["ticket_id"] = ticket.get("ticket_id")
                except Exception as e:
                    logger.warning("[OpsAPI] 创建故障工单失败: %s", e)

            # 受影响主机（CMDB 部署拓扑）：按报告 affected_services 从 evidence.hosts 映射
            hosts_map = evidence.get("hosts", {})
            report["affected_hosts"] = [
                host
                for svc in report.get("affected_services", [])
                for host in hosts_map.get(svc, [])
            ]

            # 记录 trace 输出：report 摘要 + degraded + ticket_id（如有）
            if trace_span is not None:
                trace_span.set_trace_io(
                    input=req.query,
                    output={
                        "summary": report.get("summary"),
                        "root_cause": report.get("root_cause"),
                        "confidence": report.get("confidence"),
                        "affected_services": report.get("affected_services"),
                        "degraded": coll.get("degraded", []),
                        "ticket_id": report.get("ticket_id"),
                    },
                )
            _emit_sse_stage("done")
            yield _sse("done", {"report": report, "degraded": coll.get("degraded", [])})
        except Exception as e:
            logger.warning("[OpsAPI] 诊断流异常: %s", e)
            # 诊断异常：trace 记 ERROR（SSE 仍按现有行为返回 error 事件）
            if trace_span is not None:
                trace_span.update(level="ERROR", status_message=str(e)[:2000])
            _emit_sse_stage("error")
            yield _sse("error", {"message": str(e)})


@router.post("/diagnose")
async def diagnose_api(
    req: DiagnoseRequest,
    user: dict = Depends(get_current_user),
):
    """SSE 流式诊断接口。"""
    return StreamingResponse(_diagnose_stream(req), media_type=_SSE_MEDIA_TYPE)


@router.post("/scenario")
async def set_scenario_api(
    req: ScenarioRequest,
    user: dict = Depends(get_current_user),
):
    """设置活动故障场景（演示用）。name=None 恢复无故障。"""
    try:
        name = await ops_ds.set_active_scenario(req.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"active_scenario": name}


@router.get("/scenarios")
async def list_scenarios_api(user: dict = Depends(get_current_user)):
    """列出预置故障场景。"""
    return {"scenarios": await ops_ds.list_scenarios()}


@router.get("/services")
async def list_services_api(user: dict = Depends(get_current_user)):
    """列出服务拓扑。"""
    services = await ops_ds.list_services()
    metrics = await ops_ds.list_metrics(services[0]) if services else []
    return {
        "services": services,
        "metrics": metrics,
        "source_mode": await ops_ds.get_source_mode(),
    }


@router.get("/metrics/{service}/{metric}")
async def query_metric_api(
    service: str,
    metric: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
    user: dict = Depends(get_current_user),
):
    """查询指标时序。"""
    points = await ops_ds.query_metric(service, metric, start_ts, end_ts)
    return {"service": service, "metric": metric, "points": points}
