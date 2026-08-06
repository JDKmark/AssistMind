"""电商客服 Agent 工具调用成功率评估脚本。

用法（在 backend/ 目录下执行）：
    venv\\Scripts\\python.exe scripts/run_eval_agent.py

流程：
1. LLM 连通性预检（DeepSeek → Ollama 自动降级），全部不可用则报错并以退出码 1 终止
2. 进程内启动 MCP Server（uvicorn 挂在随机端口，streamable_http 传输），
   MCPClient 连接本地端点；电商业务数据源为固定演示数据（mall 门面 mock 单实现），
   全程不依赖外部 Docker 服务
3. 从 knowledge/mall 构建 BM25 内存索引（Qdrant 不可用时知识检索仍可用，BM25 一等公民）
4. 依次执行 8 个多轮客服任务（任务列表见 TASKS），每轮调用真实 ToolAgent.run()，
   通过 history 注入上文，断言工具调用序列与最终回答
5. 输出逐任务 通过/失败 + 原因，末尾成功率（x/N）

退出码：
- 0：评估正常完成
- 1：LLM 不可用或脚本运行异常（未执行/未完成评估）
- 2：进程内 MCP Server 启动失败

说明：
- LLM_FALLBACK_TIMEOUT 放宽到 60s：本地 Ollama 推理较慢（qwen3 带思考链），
  沿用运行前设置环境变量的方式（与 run_eval_ops.py 强制 OPS_DATA_SOURCE=mock 同思路），
  只影响超时时长，不改变降级链路语义。
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import json
import logging
import os
import socket
import sys
import time

# ===== 运行前环境设置（必须在 import 任何 app.* 模块之前） =====
# 本地 Ollama 推理较慢，放宽备用 provider 超时（离线评估，非交互链路）
os.environ.setdefault("LLM_FALLBACK_TIMEOUT", "60")
# 关闭 tqdm 进度条（sentence-transformers 加载/编码模型时输出到 stdout）
os.environ["TQDM_DISABLE"] = "1"

import uvicorn  # noqa: E402

from app.agents.tool_agent import ToolAgent  # noqa: E402
from app.core.infra.llm_factory import LLMUnavailableError, call_llm  # noqa: E402
from app.core.mall import data_source as mall_ds  # noqa: E402
from app.core.mcp.client import MCPClient  # noqa: E402
from app.core.mcp.server import get_mcp_app, get_mcp_session_manager  # noqa: E402
from app.core.rag.bm25 import get_bm25  # noqa: E402
from app.core.rag.chunking import chunk_text  # noqa: E402

logger = logging.getLogger(__name__)

# backend/scripts/run_eval_agent.py → 仓库根 knowledge/mall（BM25 兜底来源，与 seed_mall_kb.py 一致）
MALL_KB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "knowledge", "mall")
_KB_EXTS = (".md", ".sql", ".yml")


# ===== 断言辅助 =====


def _names(turn: dict) -> list[str]:
    """本轮调用的工具名列表。"""
    return [tc.get("name", "") for tc in turn.get("tool_calls", [])]


def _find(turn: dict, name: str) -> dict | None:
    """查找本轮指定名称的工具调用（第一个）。"""
    return next((tc for tc in turn.get("tool_calls", []) if tc.get("name") == name), None)


def _arg(tc: dict | None, key: str, default=None):
    """取工具调用参数。"""
    if tc is None:
        return default
    return (tc.get("input") or {}).get(key, default)


def _any_has(ctx: dict, name: str) -> bool:
    """上下文（已执行轮次）中任意一轮调用过指定工具。"""
    return any(name in _names(t) for t in ctx["turns"])


def _result_of(turn: dict, name: str) -> dict | list | None:
    """返回指定工具调用的 result。"""
    tc = _find(turn, name)
    return tc.get("result") if tc is not None else None


# ===== 任务断言（每个 check(ctx, turn) -> (ok, reason)） =====
# ctx = {"turns": [已执行轮次结果...]}，turn 为当前轮次结果
# turn = {"user", "answer", "tool_calls", "iterations", "degraded"}


def check_order_query(ctx: dict, turn: dict) -> tuple[bool, str]:
    """查订单：调用 query_order(20240801001) 且返回已发货，回答含状态。"""
    tc = _find(turn, "query_order")
    if tc is None:
        return False, f"未调用 query_order（实际工具: {_names(turn) or '无'}）"
    if _arg(tc, "order_sn") != "20240801001":
        return False, f"query_order 参数错误: {tc.get('input')}"
    result = tc.get("result") or {}
    if result.get("status") != "已发货":
        return False, f"query_order 未返回「已发货」: {str(result)[:80]}"
    if "已发货" not in turn["answer"]:
        return False, f"最终回答未包含订单状态「已发货」: {turn['answer'][:60]}"
    return True, "query_order 调用成功且返回已发货"


def check_logistics_query(ctx: dict, turn: dict) -> tuple[bool, str]:
    """查物流：调用 query_logistics(20240801001)，回答含「运输中」轨迹。"""
    tc = _find(turn, "query_logistics")
    if tc is None:
        return False, f"未调用 query_logistics（实际工具: {_names(turn) or '无'}）"
    if _arg(tc, "order_sn") != "20240801001":
        return False, f"query_logistics 参数错误: {tc.get('input')}"
    if "运输中" not in turn["answer"]:
        return False, f"最终回答未包含物流轨迹「运输中」: {turn['answer'][:60]}"
    return True, "query_logistics 命中订单 20240801001 且回答含「运输中」"


def check_product_price(ctx: dict, turn: dict) -> tuple[bool, str]:
    """商品咨询：回答含价格 6999，且走 query_product 或知识库路径（两种都算成功）。"""
    if "6999" not in turn["answer"]:
        return False, f"回答未包含价格 6999: {turn['answer'][:60]}"
    names = _names(turn)
    if "query_product" not in names and "search_knowledge" not in names:
        return False, f"未走 query_product 或知识库检索路径（实际: {names or '无'}）"
    return True, "回答含价格 6999（query_product / 知识库路径均可）"


def check_refund_turn1(ctx: dict, turn: dict) -> tuple[bool, str]:
    """退货第一轮：缺订单号，不得直接申请退款，必须澄清索要订单号。"""
    if _find(turn, "apply_refund") is not None:
        return False, "第一轮不应直接申请退款（用户未提供订单号）"
    answer = turn["answer"]
    if not any(k in answer for k in ("订单号", "哪个订单", "哪一笔", "哪一单", "请提供")):
        return False, f"第一轮未澄清索要订单号: {answer[:60]}"
    return True, "未直接退款，已澄清索要订单号"


def check_refund_turn2(ctx: dict, turn: dict) -> tuple[bool, str]:
    """退货第二轮：给出订单号后，应查订单确认（或直接成功申请退款）。"""
    qtc = _find(turn, "query_order")
    if qtc is not None and _arg(qtc, "order_sn") == "20240801001":
        return True, "已调用 query_order 确认订单 20240801001"
    atc = _find(turn, "apply_refund")
    if atc is not None and _arg(atc, "order_sn") == "20240801001":
        result = atc.get("result") or {}
        if result.get("refund_id") == "AF20240801001":
            return True, "未单独查订单，但已成功申请退款 AF20240801001（宽松通过）"
    calls = [
        f"{t.get('name')}({json.dumps(t.get('input') or {}, ensure_ascii=False)})"
        for t in turn.get("tool_calls", [])
    ]
    return False, f"第二轮未正确查订单/申请退款（工具链: {calls or '无'}）"


def check_refund_turn3(ctx: dict, turn: dict) -> tuple[bool, str]:
    """退货第三轮：apply_refund 成功（AF20240801001），回答含售后单号。"""
    atc = _find(turn, "apply_refund")
    if atc is None:
        # 退款申请已在上一轮完成且回答含售后单号 → 同样通过
        if _any_has(ctx, "apply_refund") and "AF20240801001" in ctx["turns"][-1]["answer"]:
            return True, "退款申请已在上一轮完成，回答含售后单号 AF20240801001"
        return False, f"第三轮未调用 apply_refund（实际工具: {_names(turn) or '无'}）"
    if _arg(atc, "order_sn") != "20240801001":
        return False, f"apply_refund 参数错误: {atc.get('input')}"
    result = atc.get("result") or {}
    if result.get("refund_id") != "AF20240801001":
        return False, f"apply_refund 未成功返回 AF20240801001: {str(result)[:80]}"
    if "AF20240801001" not in turn["answer"]:
        return False, f"最终回答未包含售后单号 AF20240801001: {turn['answer'][:60]}"
    return True, "apply_refund 成功（AF20240801001）且回答含售后单号"


def check_refund_reject(ctx: dict, turn: dict) -> tuple[bool, str]:
    """待付款退款被拒：apply_refund 失败，回答含「待付款」原因。"""
    atc = _find(turn, "apply_refund")
    if atc is None:
        return False, f"未调用 apply_refund（实际工具: {_names(turn) or '无'}）"
    if _arg(atc, "order_sn") != "20240801004":
        return False, f"apply_refund 参数错误: {atc.get('input')}"
    result = atc.get("result") or {}
    if result.get("status") != "failed":
        return False, f"apply_refund 未被拒绝: {str(result)[:80]}"
    if "待付款" not in turn["answer"]:
        return False, f"回答未说明「待付款」拒绝原因: {turn['answer'][:60]}"
    return True, "apply_refund 被拒绝且回答说明待付款原因"


def check_order_not_found(ctx: dict, turn: dict) -> tuple[bool, str]:
    """未找到订单：如实告知不存在，并建议转人工。"""
    qtc = _find(turn, "query_order")
    if qtc is None:
        return False, f"未调用 query_order（实际工具: {_names(turn) or '无'}）"
    result = qtc.get("result") or {}
    if "不存在" not in str(result):
        return False, f"query_order 未返回订单不存在: {str(result)[:80]}"
    answer = turn["answer"]
    if not any(k in answer for k in ("不存在", "未找到", "没有找到", "查不到")):
        return False, f"回答未如实告知订单不存在: {answer[:60]}"
    if "人工" not in answer:
        return False, f"回答未建议转人工: {answer[:60]}"
    return True, "如实告知订单不存在并建议转人工"


def check_service_promise(ctx: dict, turn: dict) -> tuple[bool, str]:
    """服务承诺咨询：回答含「快速退款」（P003 配置了 service_ids 1,2,3）。"""
    if "快速退款" not in turn["answer"]:
        return False, f"回答未包含「快速退款」: {turn['answer'][:60]}"
    names = _names(turn)
    if "search_knowledge" not in names and "query_product" not in names:
        return False, f"未走知识库或 query_product 路径（实际: {names or '无'}）"
    return True, "回答命中 P003 快速退款承诺"


def check_refund_timeline(ctx: dict, turn: dict) -> tuple[bool, str]:
    """退款时限咨询：回答含「工作日（3 个工作日）」或快速退款「24 小时」。"""
    answer = turn["answer"]
    if "工作日" not in answer and "24" not in answer:
        return False, f"回答未包含退款时限（3 个工作日/24 小时）: {answer[:60]}"
    if "search_knowledge" not in _names(turn):
        return False, "未走知识库检索路径"
    return True, "回答命中退款时限承诺（3 个工作日 / 快速退款 24 小时）"


# ===== 任务列表（多轮客服任务） =====
# reset_mall=True 的任务在执行前重置 mall 数据源（清空进程内售后单记录），
# 保证 apply_refund 结果确定性（AF{order_sn} 新创建而非幂等复用）
TASKS: list[dict] = [
    {
        "name": "查订单（query_order 命中）",
        "turns": [
            {"user": "查一下订单 20240801001", "check": check_order_query},
        ],
    },
    {
        "name": "查物流（query_logistics 命中）",
        "turns": [
            {"user": "帮我查物流 20240801001", "check": check_logistics_query},
        ],
    },
    {
        "name": "商品咨询（价格命中）",
        "turns": [
            {"user": "华为 Mate 60 Pro 多少钱？", "check": check_product_price},
        ],
    },
    {
        "name": "退货流程（多轮：澄清→查单→退款）",
        "reset_mall": True,
        "turns": [
            {"user": "我要退货", "check": check_refund_turn1},
            {"user": "订单号是 20240801001", "check": check_refund_turn2},
            {"user": "帮我申请退货，原因选不想要了", "check": check_refund_turn3},
        ],
    },
    {
        "name": "待付款退款被拒",
        "reset_mall": True,
        "turns": [
            {"user": "退掉 20240801004，原因不想要了", "check": check_refund_reject},
        ],
    },
    {
        "name": "未找到订单（如实告知 + 转人工）",
        "turns": [
            {"user": "订单 999999 查一下", "check": check_order_not_found},
        ],
    },
    {
        "name": "商品服务承诺咨询（P003 快速退款）",
        "turns": [
            {"user": "P003 支持快速退款吗？", "check": check_service_promise},
        ],
    },
    {
        "name": "退款时限咨询（知识库路径）",
        "turns": [
            {"user": "退货审核通过后，退款多久到账？", "check": check_refund_timeline},
        ],
    },
]


# ===== 进程内 MCP Server =====


async def start_mcp_server() -> tuple[uvicorn.Server, asyncio.Task, object, int]:
    """在随机端口启动进程内 MCP Server（streamable_http），返回 (server, task, session_cm, port)。

    与 app/main.py 一致：挂载的 mcp 子 app 的 lifespan 不会被调用，
    需手动进入 get_mcp_session_manager().run() 初始化 task group。
    uvicorn 以 lifespan="off" 启动，避免与手动管理重复初始化。
    """
    mcp_app = get_mcp_app()
    session_cm = get_mcp_session_manager().run()
    await session_cm.__aenter__()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(
        mcp_app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            return server, server_task, session_cm, port
        await asyncio.sleep(0.05)
    raise RuntimeError("进程内 MCP Server 启动超时")


async def stop_mcp_server(server, server_task, session_cm) -> None:
    """停止进程内 MCP Server 并退出 session manager。"""
    server.should_exit = True
    with contextlib.suppress(Exception):
        await asyncio.wait_for(server_task, timeout=10)
    with contextlib.suppress(Exception):
        await session_cm.__aexit__(None, None, None)


# ===== BM25 索引（knowledge/mall → 进程内 BM25，Qdrant 不可用时知识检索兜底） =====


def _load_mall_docs() -> list[dict]:
    """读取 knowledge/mall/ 全部文档（与 seed_mall_kb.py 同口径，SOURCES.md 排除）。"""
    docs = []
    for path in sorted(glob.glob(os.path.join(MALL_KB_DIR, "**", "*.*"), recursive=True)):
        if not path.lower().endswith(_KB_EXTS):
            continue
        if os.path.basename(path).lower() == "sources.md":
            continue
        rel = os.path.relpath(path, MALL_KB_DIR).replace("\\", "/")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        docs.append(
            {
                "doc_id": os.path.splitext(rel)[0],
                "title": os.path.splitext(os.path.basename(path))[0],
                "rel_path": rel,
                "text": text,
            }
        )
    return docs


def build_bm25_index() -> int:
    """从 knowledge/mall 源文档构建 BM25 索引，返回 chunk 数。"""
    chunks = []
    for doc in _load_mall_docs():
        chunks.extend(
            chunk_text(
                doc["text"],
                metadata={
                    "doc_id": doc["doc_id"],
                    "title": doc["title"],
                    "source": f"knowledge/mall/{doc['rel_path']}",
                    "category": "mall",
                    "security_group": ["user", "agent", "admin"],
                },
            )
        )
    if chunks:
        get_bm25().build(chunks)
    return len(chunks)


# ===== 执行 =====


def _fmt_tools(turn: dict) -> str:
    """格式化工具调用链（含参数与结果摘要）。"""
    parts = []
    for tc in turn.get("tool_calls", []):
        args = json.dumps(tc.get("input") or {}, ensure_ascii=False)
        result = str(tc.get("result"))[:60]
        parts.append(f"{tc.get('name')}({args}) → {result}")
    return "\n        ".join(parts) if parts else "（无工具调用）"


def _print_turn(idx: int, turn: dict, passed: bool, reason: str) -> None:
    """打印单轮结果（输入/工具链/回答截断/判定）。"""
    answer = turn["answer"]
    if len(answer) > 80:
        answer = answer[:80] + "…"
    print(f"  轮次 {idx}: {turn['user']}")
    print(f"    工具链 : {_fmt_tools(turn)}")
    print(f"    回答   : {answer}")
    if turn.get("degraded"):
        print("    警告   : 本轮触发降级（degraded=True）")
    print(f"    判定   : {'通过' if passed else '失败'}（{reason}）")


async def run_task(agent: ToolAgent, task: dict) -> dict:
    """执行一个多轮任务：逐轮 run() 并注入 history，再逐轮断言。"""
    history: list[dict[str, str]] = []
    turn_results: list[dict] = []

    for turn_spec in task["turns"]:
        result = await agent.run(turn_spec["user"], history=history)
        turn_result = {
            "user": turn_spec["user"],
            "answer": result.get("answer", ""),
            "tool_calls": result.get("tool_calls", []),
            "iterations": result.get("iterations", 0),
            "degraded": bool(result.get("degraded")),
        }
        turn_results.append(turn_result)
        history.append({"role": "user", "content": turn_spec["user"]})
        history.append({"role": "assistant", "content": turn_result["answer"]})

    ctx = {"turns": turn_results}
    checks = []
    ok = True
    for i, turn_spec in enumerate(task["turns"], 1):
        passed, reason = turn_spec["check"](ctx, turn_results[i - 1])
        if not passed:
            ok = False
        checks.append({"passed": passed, "reason": reason})
    return {"task": task, "turns": turn_results, "checks": checks, "ok": ok}


async def main() -> None:
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(line_buffering=True)

    # ---- LLM 连通性预检（DeepSeek → Ollama 自动降级） ----
    print("[信息] LLM 连通性预检中（DeepSeek → Ollama 自动降级）…")
    try:
        resp = await call_llm("只回复两个字符：OK", system="你是连通性测试助手，只回复 OK。")
        print(f"[信息] LLM 预检通过，响应: {str(resp).strip()[:60]}")
    except LLMUnavailableError as e:
        print(f"[错误] LLM 不可用（DeepSeek 与 Ollama 均失败）: {e}", file=sys.stderr)
        print(
            "[提示] 请检查 DEEPSEEK_API_KEY 配置或本机 Ollama 服务（默认 http://localhost:11434），"
            "修复后重跑本脚本。",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"[错误] LLM 预检异常: {e!r}", file=sys.stderr)
        sys.exit(1)

    # ---- 进程内 MCP Server ----
    print("[信息] 启动进程内 MCP Server（随机端口）…")
    server, server_task, session_cm, port = await start_mcp_server()
    print(f"[信息] MCP Server 已启动: http://127.0.0.1:{port}/（工具走 mall 演示数据源）")
    client = MCPClient(server_url=f"http://127.0.0.1:{port}/")
    try:
        # ---- BM25 索引（Qdrant 不可用时知识检索兜底） ----
        bm25_chunks = build_bm25_index()
        print(f"[信息] BM25 索引构建完成（knowledge/mall，{bm25_chunks} 个 chunk，Qdrant 不依赖）")

        # ---- 逐任务评估 ----
        agent = ToolAgent(mcp_client=client)
        print(f"\n===== 客服 Agent 工具调用成功率评估（{len(TASKS)} 个任务） =====")
        results: list[dict] = []
        for idx, task in enumerate(TASKS, 1):
            if task.get("reset_mall"):
                mall_ds.reset_source()
                print(f"[信息] 任务「{task['name']}」前已重置 mall 数据源（售后单记录清空）")
            started = time.perf_counter()
            result = await run_task(agent, task)
            elapsed = time.perf_counter() - started

            print(f"\n[任务 {idx}/{len(TASKS)}] {task['name']}")
            for i, (turn, check) in enumerate(zip(result["turns"], result["checks"]), 1):
                _print_turn(i, turn, check["passed"], check["reason"])
            print(f"  耗时: {elapsed:.1f}s  结果: {'通过' if result['ok'] else '失败'}")
            results.append(result)

        # ---- 汇总 ----
        passed = sum(1 for r in results if r["ok"])
        total = len(results)
        print("\n===== 评估结果 =====")
        print(f"总体成功率: {passed}/{total}（{passed / total * 100:.1f}%）")
        failed = [r["task"]["name"] for r in results if not r["ok"]]
        if failed:
            print(f"失败任务: {', '.join(failed)}")
            for r in results:
                if r["ok"]:
                    continue
                for i, check in enumerate(r["checks"], 1):
                    if not check["passed"]:
                        print(f"  - {r['task']['name']} / 轮次 {i}（{r['turns'][i - 1]['user']}）: {check['reason']}")
    finally:
        await client.close()
        await stop_mcp_server(server, server_task, session_cm)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断，评估中止。", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"[错误] 评估脚本运行异常: {e!r}", file=sys.stderr)
        sys.exit(1)
