"""真实 HTTP 接口冒烟测试：requests（非 TestClient/httpx）+ uvicorn 子进程。

覆盖目标：让项目测试真正覆盖「requests + pytest」这一接口测试手段，验证
FastAPI 应用经真实 TCP 栈端到端可用（不含任何进程内 mock / WSGI 直调）。

运行前提（与 .github/workflows/ci.yml service 配置一致的最小依赖）：
- PostgreSQL 15：postgres:15-alpine @ 5432（DATABASE_URL 指向 assistmind 库）
- Redis 7：redis:7-alpine @ 26379（主机 6379 被 Windows 保留段占用时改用 26379）
- Qdrant 可缺席（连接失败会按既有降级链退化为仅 BM25 / 未找到）

fixture（live_server）设计：
- 随机端口 127.0.0.1 上以子进程启动 uvicorn 指向 app.main:app，
  测试结束后 terminate（不留在后台）
- LLM/外网依赖在子进程 env 内按「最小依赖能跑通」注入：
  DEEPSEEK/OLLAMA_BASE_URL 指向本服务自身不存在的 /v1 路径（真实在听端口、
  404 瞬时失败）→ 走项目既定降级链（改写降级→检索→模板兜底），断言不依赖真实大模型
- 关闭重排（RERANKER_ENABLED=false）：本地 CrossEncoder 一次 1-2 分钟，
  冒烟场景不需要精排，避免拖慢用例
- Langfuse key 置空：避免读 backend/.env 真实 key 造成埋点外呼（no-op 语义）
- MCP_SERVER_URL 指向本服务自身 /mcp/：task 链路的 MCP 握手走真实传输层

用例清单（5 个）：
1. test_health_returns_200        GET  /api/v1/health → 200，status=ok + 依赖树齐全
2. test_chat_faq_streams_answer   POST /api/v1/chat/ask 知识类（faq 意图）
                                  → 200，text/event-stream，start 报 faq，done 含 answer
3. test_chat_task_streams_answer  POST /api/v1/chat/ask 业务类（task 意图，订单查询）
                                  → 200，start 报 task，done 含 answer
4. test_chat_invalid_body_4xx     POST /api/v1/chat/ask 空 query / 缺 query / 非法 JSON
                                  → 422（异常输入边界）
5. test_unknown_route_404         GET  /api/v1/no-such-route → 404

备注：本文件位于 tests/integration/，CI 单测 job 的 `-k "not integration"` 天然排除；
`requests_smoke` marker 已注册（backend/pyproject.toml），可用 -m requests_smoke 显式挑选。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests
from jose import jwt

pytestmark = [
    pytest.mark.integration,  # 需要 Docker 服务（postgres/redis），与既有 integration 语义一致
    pytest.mark.requests_smoke,
]

BACKEND_DIR = Path(__file__).resolve().parents[2]

# 与子进程环境一致的 JWT_SECRET：测试进程侧用 python-jose 直接签发，
# 不读本进程 Settings（避免 lru_cache 环境污染），子进程解码用同一密钥。
TEST_JWT_SECRET = "requests-smoke-test-jwt-secret-0123456789"


def _make_token() -> str:
    """签发与子进程 JWT_SECRET 匹配的测试 token（admin 角色，uid 齐全免 PG 回查）。"""
    payload = {
        "uid": "smoke-tester-id",
        "sub": "smoke_tester",
        "role": "admin",
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


AUTH_HEADERS = {"Authorization": f"Bearer {_make_token()}"}

# 真实 HTTP 会话：trust_env=False 忽略系统 HTTP(S)_PROXY——
# 本机 shell 常带代理 env（TRAE 沙箱注入 HTTP_PROXY=http://127.0.0.1:11888），
# 与本地死端口/随机端口组合会命中无监听端口或代理转发失败，冒烟必须直连回环。
HTTP = requests.Session()
HTTP.trust_env = False
HTTP.proxies.clear()


def _parse_sse(text: str) -> list[tuple[str, dict | None]]:
    """解析 SSE 文本为 [(event, data), ...]（事件间以空行分隔，格式与 chat.py 一致）。"""
    events: list[tuple[str, dict | None]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event: str | None = None
        data: dict | None = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: "):].strip()
            elif line.startswith("data: "):
                raw = line[len("data: "):]
                data = json.loads(raw) if raw else None
        if event:
            events.append((event, data))
    return events


@pytest.fixture(scope="module")
def live_server() -> str:
    """后台启动 uvicorn（127.0.0.1 随机端口）指向 app，测试结束关闭。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    base_url = f"http://127.0.0.1:{port}"

    # 最小依赖可跑通：注入测试专属 env（覆盖无效/真实外部依赖，不改生产代码）
    env = os.environ.copy()
    env.update(
        {
            # 基础设施（复用本机已在跑的 docker 服务，端口同 CI 约定/任务要求）
            "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@localhost:5432/assistmind",
            "REDIS_URL": "redis://localhost:26379/0",
            # 应用安全
            "JWT_SECRET": TEST_JWT_SECRET,
            "DEBUG": "true",
            # LLM 指向本服务自身的 /v1（真实在听端口）：该路径不存在 → 404 快速失败，
            # 走项目既定降级链（改写降级→检索→模板兜底），不依赖公网/真实模型。
            # 不用「本机死端口」——在部分受限环境（CI 沙箱代理）里连接未监听端口会
            # 被黑洞化走满超时（曾致 faq 链路每次 LLM 调用 10-30s×N 次），指向自服务
            # 的 404 在任何环境都是确定性瞬时失败。
            "DEEPSEEK_API_KEY": "dummy",
            "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "OLLAMA_BASE_URL": f"http://127.0.0.1:{port}",
            # 冒烟场景确定性：mock 业务数据源、跳过本地重排（一次 1-2 分钟）、禁用埋点
            "MALL_DATA_SOURCE": "mock",
            "RERANKER_ENABLED": "false",
            "LANGFUSE_PUBLIC_KEY": "",
            "LANGFUSE_SECRET_KEY": "",
            # MCP 指向本服务自身（真实 streamable_http 握手），随端口动态注入
            "MCP_SERVER_URL": f"http://127.0.0.1:{port}/mcp/",
            # HF 离线模式：embedding 模型（BAAI/bge-base-zh-v1.5）已在本机缓存，
            # 禁掉 transformers 对 huggingface.co 的网络探测——受控环境（CI 沙箱代理）
            # 下这类探测会每个请求黑洞化走满超时，曾致 faq 在「加载模型」前卡 40s+。
            # 离线后冷加载 ~25s（一次性，faq 用例的读超时已按此预留）。
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            # 移除系统代理：requests 会把本地 127.0.0.1 请求走代理，导致超时
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    # 移除 HTTP_PROXY/HTTPS_PROXY（强制绕开本地代理对回环请求的转发）
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
        env.pop(k, None)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-B",  # 不写 pyc（venv 基解释器在 C:\Python313，沙箱限制其写入，-B 直接跳过）
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + 180  # 冷启动含大依赖导入（langchain/qdrant/mcp），首建连接较慢
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"uvicorn 提前退出，exit={proc.returncode}")
            try:
                resp = HTTP.get(f"{base_url}/api/v1/health", timeout=2)
                if resp.status_code == 200 and resp.json().get("status") == "ok":
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("uvicorn 180s 内未就绪（/api/v1/health 未返回 200）")
    except BaseException:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise

    yield base_url

    # 测试结束关闭服务
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


# ---------- ① 健康检查 ----------


def test_health_returns_200(live_server):
    """GET /api/v1/health → 200，应用状态 ok，依赖探测树齐全。"""
    resp = HTTP.get(f"{live_server}/api/v1/health", timeout=5)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] and body["version"]
    # 四个依赖探测项齐全（qdrant/redis/postgres 本机服务可用；langfuse 未配置为 disabled）
    assert {"qdrant", "redis", "postgres", "langfuse"} == set(body["dependencies"])


# ---------- ② 知识类问题（faq 意图） ----------


def test_chat_faq_streams_answer(live_server):
    """POST /api/v1/chat/ask 知识类问题 → 200 + SSE，start 报 faq 意图，done 含 answer。

    无知识库语料时命中项目既定降级链（未找到相关文档/模板兜底），
    冒烟只断言链路端到端可用（状态码 + 事件结构），不限定答案内容。
    """
    resp = HTTP.post(
        f"{live_server}/api/v1/chat/ask",
        headers=AUTH_HEADERS,
        json={"query": "如何配置系统"},
        timeout=(3, 90),
    )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/event-stream")

    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names[0] == "start"
    assert names[-1] == "done"

    ed = {e: d for e, d in events}
    assert ed["start"]["intent"] == "faq"
    assert "answer" in ed["done"]
    assert ed["done"]["answer"]


# ---------- ③ 业务类问题（task 意图，订单查询） ----------


def test_chat_task_streams_answer(live_server):
    """POST /api/v1/chat/ask 业务类问题 → 200 + SSE，start 报 task 意图，done 含 answer。

    LLM 决策在 dummy key + 指向自服务 /v1（404）下走 Agent 降级话术，仍以 done 正常收尾；
    状态码与事件契约是冒烟关注点。
    """
    resp = HTTP.post(
        f"{live_server}/api/v1/chat/ask",
        headers=AUTH_HEADERS,
        json={"query": "查一下我的订单状态"},
        timeout=(3, 120),
    )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/event-stream")

    events = _parse_sse(resp.text)
    names = [e for e, _ in events]
    assert names[0] == "start"
    assert names[-1] == "done"

    ed = {e: d for e, d in events}
    assert ed["start"]["intent"] == "task"
    assert "answer" in ed["done"]


# ---------- ④ 空/非法请求体（异常输入边界） ----------


def test_chat_invalid_body_returns_4xx(live_server):
    """POST /api/v1/chat/ask 异常输入：空 query / 缺 query / 非法 JSON → 422。"""
    # 空 query（query min_length=1 校验）
    resp_empty = requests.post(
        f"{live_server}/api/v1/chat/ask",
        headers=AUTH_HEADERS,
        json={"query": ""},
        timeout=5,
    )

    # 缺 query 必填字段
    resp_missing = requests.post(
        f"{live_server}/api/v1/chat/ask",
        headers=AUTH_HEADERS,
        json={"history": []},
        timeout=5,
    )

    # 非法 JSON 请求体
    resp_malformed = requests.post(
        f"{live_server}/api/v1/chat/ask",
        headers={**AUTH_HEADERS, "Content-Type": "application/json"},
        data="{not-valid-json",
        timeout=5,
    )

    assert resp_empty.status_code == 422
    assert resp_missing.status_code == 422
    assert resp_malformed.status_code == 422


# ---------- ⑤ 未知路由 ----------


def test_unknown_route_returns_404(live_server):
    """GET 未知路由 → 404（FastAPI 默认 NOT_FOUND 语义）。"""
    resp = HTTP.get(f"{live_server}/api/v1/no-such-route", timeout=5)

    assert resp.status_code == 404
    assert resp.json().get("detail") == "Not Found"