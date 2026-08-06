"""根因命中率评估脚本：诊断预置故障场景，统计根因命中率。

用法（在 backend/ 目录下执行）：
    venv\\Scripts\\python.exe scripts/run_eval_ops.py

流程：
1. 强制 OPS_DATA_SOURCE=mock：门面恒用 MockOpsDataSource（预置场景数据），不依赖 Prometheus
2. LLM 连通性预检（DeepSeek → Ollama 自动降级），全部不可用则报错并以退出码 1 终止
3. 依次激活 3 个预置故障场景，调用 OpsSupervisorAgent.run() 执行完整诊断
4. 把报告 root_cause 与场景预期关键词比对，输出逐场景结果与总体命中率

退出码：
- 0：评估正常完成
- 1：LLM 不可用或脚本运行异常（未执行/未完成评估）
- 2：mock 门面未生效（数据源不是 MockOpsDataSource）
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sys
import time

# ===== 强制 mock 数据源（必须在 import 任何 app.* 模块之前） =====
# data_source.py 门面在模块导入时执行 settings = get_settings()（lru_cache 单例），
# _resolve_source() 按 settings.OPS_DATA_SOURCE 决定实现；pydantic 环境变量优先级高于 .env，
# 因此这里先设置环境变量，门面首次解析时 mode=="mock" 恒返回 MockOpsDataSource，
# 全程不会对 Prometheus 做健康探测/查询。
os.environ["OPS_DATA_SOURCE"] = "mock"

# 关闭 tqdm 进度条（sentence-transformers 加载/编码模型时输出到 stdout），
# 避免干扰评估结果的可读性；对脚本功能无影响。
os.environ["TQDM_DISABLE"] = "1"

from app.agents.ops_supervisor import OpsSupervisorAgent
from app.config import Settings
from app.core.infra.llm_factory import LLMUnavailableError, call_llm
from app.core.ops import data_source as ops_ds
from app.core.ops.scenarios import SCENARIOS, OpsScenario

logger = logging.getLogger(__name__)

# ===== 预期根因关键词（人工精选，每场景 1-2 个） =====
# 不用「root_cause 去标点后取长度>=2 词元」的机械规则：中文根因文本里大量高频
# 连接词/程度词（"导致""由于""超过""大量"）长度均>=2，会产生很多假阳性；
# 人工关键词直接锁定「根因实体」（连接池 / 慢SQL / 内存），命中即代表 LLM
# 识别出了正确的故障类型，判定更稳。选取依据见各场景注释。
EXPECTED_KEYWORDS: dict[str, list[str]] = {
    # conn_pool_exhausted：根因 = 配置变更把连接池 max_pool_size 从 50 缩小到 10，连接被耗尽
    "conn_pool_exhausted": ["连接池", "max_pool_size"],
    # slow_sql：根因 = status 字段未建索引，高并发全表扫描产生慢 SQL。实测 LLM 措辞在
    # 「慢SQL / 全表扫描 / 未建索引」之间变化（三者为同一根因的不同表述），
    # 取出现率最高的两个实体词，避免因措辞差异误判未命中
    "slow_sql": ["慢SQL", "全表扫描"],
    # memory_leak：根因 = 会话缓存无 TTL/容量上限，session 无限增长耗尽堆内存
    "memory_leak": ["内存", "会话缓存"],
}


def _build_query(scenario: OpsScenario) -> str:
    """由症状构造贴近一线的故障描述。

    刻意不用 title：title 括号内已带根因结论（如「（inventory-service 连接池耗尽）」），
    拼进 query 等于把答案喂给 LLM，命中率评估就失去意义；症状是用户可观测现象，不泄漏根因。
    """
    return "。".join(scenario.symptoms) + "。请诊断故障根因。"


_PUNCT_RE = re.compile(r"[\s\u3000，。、；：！？（）()\[\]【】\"'“”‘’《》<>\-_/]+")


def _normalize(text: str) -> str:
    """归一化：去空白/常见标点 + 小写，让「慢 SQL」与「慢SQL」、max_pool_size 大小写差异可互相命中。"""
    return _PUNCT_RE.sub("", text).lower()


def _is_hit(root_cause: str, expected: list[str]) -> bool:
    """命中判定：归一化后的报告 root_cause 包含任一预期关键词。"""
    normalized = _normalize(root_cause)
    return any(_normalize(kw) in normalized for kw in expected)


async def eval_one(agent: OpsSupervisorAgent, scenario: OpsScenario, expected: list[str]) -> dict:
    """对单个场景执行一次完整诊断并判定命中。"""
    # 经门面激活场景（mock 模式下即 MockOpsDataSource.set_active_scenario）
    await ops_ds.set_active_scenario(scenario.name)
    query = _build_query(scenario)
    logger.info("[EvalOps] 场景=%s 开始诊断，query=%s…", scenario.name, query[:40])

    started = time.perf_counter()
    result = await agent.run(query)
    elapsed = time.perf_counter() - started

    report = result.get("report", {}) or {}
    root_cause = str(report.get("root_cause", "") or "").strip()
    # LLM 综合失败（不可用/JSON 解析失败）时 analyze 输出规则兜底报告：
    # summary 为固定文案「已采集证据但 LLM 综合失败…」且 confidence=0.3
    llm_degraded = str(report.get("summary", "")).startswith("已采集证据")
    degraded = result.get("degraded", []) or []

    return {
        "name": scenario.name,
        "title": scenario.title,
        "expected": expected,
        "root_cause": root_cause,
        "hit": _is_hit(root_cause, expected),
        "llm_degraded": llm_degraded,
        "degraded": degraded,
        "elapsed": elapsed,
    }


def _print_row(idx: int, r: dict) -> None:
    """打印单场景评估行（场景名 / 预期关键词 / 报告根因 120 字截断 / 命中判定）。"""
    notes = []
    if r["llm_degraded"]:
        notes.append("LLM 综合失败，输出规则兜底报告")
    if r["degraded"]:
        notes.append("证据降级: " + ",".join(r["degraded"]))
    root_cause = r["root_cause"] or "（空）"
    truncated = root_cause if len(root_cause) <= 120 else root_cause[:120] + "…"

    print(f"\n[场景 {idx}/{len(SCENARIOS)}] {r['name']}")
    print(f"  场景标题   : {r['title']}")
    print(f"  预期关键词 : {' | '.join(r['expected'])}")
    print(f"  报告根因   : {truncated}")
    print(f"  耗时       : {r['elapsed']:.1f}s")
    print(f"  判定       : {'命中' if r['hit'] else '未命中'}" + (f"（{'；'.join(notes)}）" if notes else ""))


async def main() -> None:
    # print 按行刷出（管道/重定向时 Python stdout 默认块缓冲，会导致结果行与
    # 应用日志在终端里交错显示）
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(line_buffering=True)

    # ---- 数据源门面自检（双保险） ----
    # 环境变量已在模块顶部设置；这里再按测试惯例（tests/conftest.py）把门面 settings
    # 钉死为 mock 并清空进程内缓存，确保 _resolve_source() 重新解析时必走 mock 分支。
    ops_ds.settings = Settings(OPS_DATA_SOURCE="mock")
    ops_ds.reset_source()
    mode = await ops_ds.get_source_mode()
    if mode != "mock":
        print(f"[错误] mock 数据源未生效，门面实际模式={mode}，评估终止。", file=sys.stderr)
        sys.exit(2)
    print(f"[信息] 数据源门面模式 = {mode}（OPS_DATA_SOURCE=mock，不依赖 Prometheus）")
    print(f"[信息] 预置场景 {len(SCENARIOS)} 个: {', '.join(SCENARIOS)}")

    # ---- LLM 连通性预检 ----
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

    # ---- 逐场景诊断 ----
    agent = OpsSupervisorAgent()
    results: list[dict] = []
    print("\n===== 根因命中率评估（3 个预置故障场景） =====")
    for idx, scenario in enumerate(SCENARIOS.values(), 1):
        result = await eval_one(agent, scenario, EXPECTED_KEYWORDS.get(scenario.name, []))
        results.append(result)
        _print_row(idx, result)

    # ---- 汇总 ----
    hits = sum(1 for r in results if r["hit"])
    total = len(results)
    print("\n===== 评估结果 =====")
    print(f"总体命中率: {hits}/{total}（{hits / total * 100:.1f}%）")
    missed = [r["name"] for r in results if not r["hit"]]
    if missed:
        print(f"未命中场景: {', '.join(missed)}")


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
