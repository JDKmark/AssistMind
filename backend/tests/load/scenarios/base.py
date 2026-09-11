"""压测用户基类：登录复用 + SSE 问答 + 双路上报（Locust 统计 + CSV 明细）。

上报设计：
- 主请求名 ``<scenario>``：response_time = total_ms（端到端，含流式写出）
- 附加请求名 ``<scenario>:ttft``：response_time = ttft_ms（首个 delta）
  —— 专门用来在 Locust 报告里看 TTFT 分位；可用 ``LOAD_REPORT_TTFT=0`` 关闭。

注意：Locust 的 ``HttpSession`` 统计的是「响应头到达」时刻，不等于 SSE 全流时间，
因此这里不用 ``self.client`` 发请求，而是用 ``lib.sse_client`` 手工计时后
``events.request.fire`` 上报，保证 total_ms / ttft_ms 口径与 T5 指标一致。
"""

from __future__ import annotations

import os

from lib import auth, ingest
from lib.sse_client import SSEResult, ask
from locust import HttpUser, between

_REPORT_TTFT = os.getenv("LOAD_REPORT_TTFT", "1") != "0"


class AssistMindUser(HttpUser):
    """压测用户基类（abstract，不直接被 spawn）。"""

    abstract = True

    host = os.getenv("LOAD_HOST", "http://localhost:8002")
    wait_time = between(0.2, 1.0)
    scenario_name = "base"

    def on_start(self) -> None:
        """每个虚拟用户启动：初始化落盘 + 获取并复用 JWT。"""
        ingest.init(os.getenv("LOAD_CSV_DIR"))
        self.token = auth.get_token(self.host)
        self.failures = 0

    # ---- 核心：一次问答 + 上报 ----
    def ask(
        self,
        query: str,
        *,
        name: str | None = None,
        persona: str | None = None,
        history: list[dict[str, str]] | None = None,
        timeout: float = 90.0,
    ) -> SSEResult:
        result = ask(
            self.host,
            self.token,
            query,
            timeout=timeout,
            persona=persona,
            history=history,
        )
        self._report(result, name or self.scenario_name)
        return result

    def _report(self, result: SSEResult, name: str) -> None:
        row = result.as_row(self.scenario_name)
        ingest.record(row)

        if result.ok:
            exc: Exception | None = None
        else:
            self.failures += 1
            exc = RuntimeError(result.error or "sse_failed")

        # 主指标：端到端耗时
        self.environment.events.request.fire(
            request_type="SSE",
            name=name,
            response_time=max(1, result.total_ms),
            response_length=result.answer_len,
            exception=exc,
            context={
                "ttft_ms": result.ttft_ms,
                "degraded": result.degraded,
                "from_cache": result.from_cache,
                "status_code": result.status_code,
            },
        )
        # 附加指标：TTFT（首个 delta），单独一行便于看分位
        if _REPORT_TTFT and result.ttft_ms >= 0:
            self.environment.events.request.fire(
                request_type="SSE",
                name=f"{name}:ttft",
                response_time=max(1, result.ttft_ms),
                response_length=0,
                exception=None,
            )
