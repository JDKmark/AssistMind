"""SSE 请求 + TTFT 采集（Locust 同步模型下的流式客户端）。

关键实现要点（spec 3.4 的三个坑）：
1. ``iter_lines(chunk_size=1)``：不设会被 urllib3 缓冲，TTFT 被虚高。
2. gevent：Locust 运行时会 monkey patch ``requests``，流式读取不会阻塞 worker
   （本文件用模块级 ``import requests``，运行时拿到的就是已 patch 的版本）。
3. 长连接文件描述符：压测机上注意 ``ulimit -n``（SSE 是长连接，FD 很快耗尽）。

TTFT 口径：从发请求到**首个 ``delta`` 事件**写出（与 T5 ``assistmind_ttft_seconds``
一致）；缓存命中 / 澄清 / 工具直答等无 delta 的路径记为 -1。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import requests

# 压测会话：默认忽略环境代理（HTTP_PROXY/HTTPS_PROXY）。
# 原因：本机常驻代理（如 127.0.0.1:4085）会把 localhost 请求转发出去导致 ProxyError，
# 使压测结果完全失真。需要经代理访问远端时可设 LOAD_TRUST_ENV=1。
_SESSION = requests.Session()
_SESSION.trust_env = os.getenv("LOAD_TRUST_ENV", "0") == "1"


@dataclass
class SSEResult:
    """一次 SSE 问答的采集结果。"""

    query: str
    status_code: int = 0
    ttft_ms: int = -1
    total_ms: int = 0
    intent: str = ""
    from_cache: str = ""
    degraded: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    answer_len: int = 0
    tool_calls: int = 0
    error: str = ""
    ok: bool = False

    def as_row(self, scenario: str) -> dict[str, object]:
        return {
            "scenario": scenario,
            "query_len": len(self.query),
            "status_code": self.status_code,
            "ttft_ms": self.ttft_ms,
            "total_ms": self.total_ms,
            "intent": self.intent,
            "from_cache": self.from_cache,
            "degraded": "|".join(self.degraded),
            "n_degraded": len(self.degraded),
            "events": ">".join(self.events),
            "answer_len": self.answer_len,
            "tool_calls": self.tool_calls,
            "ok": int(self.ok),
            "error": self.error,
        }


def ask(
    base_url: str,
    token: str,
    query: str,
    *,
    timeout: float = 90.0,
    persona: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> SSEResult:
    """发起一次 ``POST /api/v1/chat/ask`` 并逐行解析 SSE。"""
    result = SSEResult(query=query)
    url = f"{base_url}/api/v1/chat/ask"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    payload: dict[str, object] = {"query": query}
    if persona:
        payload["persona"] = persona
    if history:
        payload["history"] = history

    t0 = time.perf_counter()
    try:
        with _SESSION.post(url, headers=headers, json=payload, stream=True, timeout=timeout) as resp:
            result.status_code = resp.status_code
            if resp.status_code != 200:
                result.error = f"http_{resp.status_code}"
                result.total_ms = round((time.perf_counter() - t0) * 1000)
                return result

            event_name: str | None = None
            for raw in resp.iter_lines(chunk_size=1):
                if raw is None:
                    continue
                line = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else raw
                if line.startswith("event: "):
                    event_name = line[len("event: "):].strip()
                    continue
                if not line.startswith("data: "):
                    continue
                data_raw = line[len("data: "):]
                try:
                    data = json.loads(data_raw) if data_raw else {}
                except json.JSONDecodeError:
                    data = {}

                if event_name:
                    result.events.append(event_name)
                if event_name == "delta":
                    if result.ttft_ms < 0:
                        result.ttft_ms = round((time.perf_counter() - t0) * 1000)
                    result.answer_len += len(str(data.get("delta", "")))
                elif event_name == "start":
                    result.intent = str(data.get("intent", ""))
                elif event_name == "tool_call":
                    result.tool_calls += 1
                elif event_name == "done":
                    result.intent = result.intent or str(data.get("intent", ""))
                    result.from_cache = str(data.get("from_cache", ""))
                    degraded = data.get("degraded")
                    if isinstance(degraded, list):
                        result.degraded = [str(x) for x in degraded]
                    result.answer_len = max(result.answer_len, len(str(data.get("answer", ""))))
                elif event_name == "error":
                    result.error = str(data.get("message", "sse_error"))

        result.total_ms = round((time.perf_counter() - t0) * 1000)
        result.ok = not result.error and "done" in result.events
        if not result.ok and not result.error:
            result.error = "no_done_event"
        return result
    except Exception as e:  # noqa: BLE001 - 压测客户端：任何异常都转成结果，不中断用户
        result.total_ms = round((time.perf_counter() - t0) * 1000)
        result.error = f"{type(e).__name__}: {e}"
        return result
