"""故障注入开关（T6，env 驱动，默认全关）。

用途：验证「降级是否正确生效」——需要可控、可复现的故障注入，把某一层打挂后
观察全链路降级表对应的路径是否被触发、warning 是否可见、degradaion 指标是否 +1、
关闭后是否无需重启即恢复。

设计要点：
- **env 驱动**：``FAULT_LLM`` / ``FAULT_RERANKER`` / ``FAULT_QDRANT``，默认全关。
- **运行时可热切换**：优先级「运行时覆盖文件 > 环境变量 > settings 默认值」。
  设置 ``FAULT_OVERRIDE_FILE`` 指向一个 JSON 文件（如
  ``{"FAULT_RERANKER": "fail"}``），进程按 mtime 热加载，因此压测中只需改写该文件
  即可开关故障、关闭后自动恢复，**无需重启进程**；未设置该变量时零开销。
- **集中收口**：故障判定与副作用集中在 ``apply_*`` 函数里，业务路径只调一次，
  不在检索/生成/客服逻辑里散落 if。
- **复用既有降级抽象**：LLM 故障在 provider core 抛出后被既有重试/断路器/降级链
  捕获；Qdrant / Reranker 故障走各自既有的「返回空 / 返回 None」降级契约。
- **旁路可观测**：命中故障时打一次 ``logger.warning`` 并给
  ``assistmind_degradation_total`` +1，绝不吞掉（否则不是「可控可复现」）。

模式约定：
- ``FAULT_LLM=ok|timeout|error|slow``
- ``FAULT_RERANKER=ok|timeout|fail``
- ``FAULT_QDRANT=ok|down``
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from app.config import get_settings
from app.core.infra import metrics

logger = logging.getLogger(__name__)
settings = get_settings()

VALID_LLM = ("ok", "timeout", "error", "slow")
VALID_RERANKER = ("ok", "timeout", "fail")
VALID_QDRANT = ("ok", "down")

# 运行时覆盖文件缓存（按 mtime 热加载）
_OVERRIDE: dict = {"mtime": None, "data": {}}


def _runtime_overrides() -> dict:
    """读取运行时覆盖文件（未配置/不存在/损坏 → 空 dict，不影响 env 语义）。"""
    path = os.environ.get("FAULT_OVERRIDE_FILE") or getattr(settings, "FAULT_OVERRIDE_FILE", "")
    if not path:
        return {}
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _OVERRIDE["mtime"] != mtime:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            _OVERRIDE["data"] = data if isinstance(data, dict) else {}
            _OVERRIDE["mtime"] = mtime
            logger.warning("[Fault] 运行时覆盖已生效: %s", _OVERRIDE["data"])
        except Exception as e:  # noqa: BLE001 - 覆盖文件损坏不改行为
            logger.warning("[Fault] 覆盖文件解析失败（忽略）: %s", e)
            return _OVERRIDE["data"] or {}
    return _OVERRIDE["data"] or {}


def _read(name: str, default: str, valid: tuple[str, ...]) -> str:
    """读取故障模式：运行时覆盖 > env > settings；非法值回落 ok 并告警。"""
    raw = _runtime_overrides().get(name)
    if raw is None:
        raw = os.environ.get(name)
    if raw is None:
        raw = getattr(settings, name, default)
    mode = str(raw or default).strip().lower()
    if mode not in valid:
        logger.warning("[Fault] %s=%r 非法，回落到 ok（可选：%s）", name, raw, "/".join(valid))
        return "ok"
    return mode


def llm_mode() -> str:
    return _read("FAULT_LLM", "ok", VALID_LLM)


def reranker_mode() -> str:
    return _read("FAULT_RERANKER", "ok", VALID_RERANKER)


def qdrant_mode() -> str:
    return _read("FAULT_QDRANT", "ok", VALID_QDRANT)


# ===== 测试 / 运维辅助：运行时开关（不重启进程）=====


def set_fault(name: str, value: str) -> None:
    """设置一个故障开关（写入 os.environ，立即生效）。"""
    os.environ[name] = str(value)


def clear_faults() -> None:
    """清空所有故障注入（恢复 ok）。"""
    for name in ("FAULT_LLM", "FAULT_RERANKER", "FAULT_QDRANT"):
        os.environ.pop(name, None)


# ===== 各层故障应用 =====


async def apply_llm_fault(provider: str) -> None:
    """在某个 LLM provider 真正调用前应用故障。

    - ok：立即返回（可直接调用）
    - slow：延迟 ``FAULT_LLM_SLOW_MS`` 后返回（首 token 变慢，但仍成功）
    - timeout：等待 ``FAULT_LLM_DELAY_MS`` 后抛 ``asyncio.TimeoutError``（可重试）
    - error：立即抛 ``RuntimeError``（不可重试，快速降级）

    抛出的异常由既有 tenacity 重试 / 断路器 / provider 降级链处理，
    本函数不改变降级语义。
    """
    mode = llm_mode()
    if mode == "ok":
        return
    why = f"fault_{mode}"
    metrics.safe(metrics.inc_degradation, "llm", why)
    if mode == "slow":
        delay = max(0, settings.FAULT_LLM_SLOW_MS) / 1000.0
        logger.warning(
            "[Fault] FAULT_LLM=slow：provider=%s 首 token 延迟 %dms",
            provider,
            settings.FAULT_LLM_SLOW_MS,
        )
        if delay:
            await asyncio.sleep(delay)
        return
    if mode == "timeout":
        delay = max(0, settings.FAULT_LLM_DELAY_MS) / 1000.0
        logger.warning(
            "[Fault] FAULT_LLM=timeout：provider=%s 等待 %dms 后超时",
            provider,
            settings.FAULT_LLM_DELAY_MS,
        )
        if delay:
            await asyncio.sleep(delay)
        raise TimeoutError(f"[FaultInjection] LLM 超时注入（provider={provider}）")
    # error
    logger.warning("[Fault] FAULT_LLM=error：provider=%s 立即失败", provider)
    raise RuntimeError(f"[FaultInjection] LLM 错误注入（provider={provider}）")


def apply_reranker_fault() -> bool:
    """应用 Reranker 故障。命中返回 True（调用方应走「跳过重排用 RRF」降级）。"""
    mode = reranker_mode()
    if mode == "ok":
        return False
    metrics.safe(metrics.inc_degradation, "reranker", f"fault_{mode}")
    hint = "超时" if mode == "timeout" else "失败"
    logger.warning("[Fault] FAULT_RERANKER=%s：重排%s，降级用 RRF 结果", mode, hint)
    return True


def apply_qdrant_fault() -> bool:
    """应用 Qdrant 故障。命中返回 True（调用方应返回空结果，降级为仅 BM25）。"""
    mode = qdrant_mode()
    if mode != "down":
        return False
    metrics.safe(metrics.inc_degradation, "qdrant", "fault_down")
    logger.warning("[Fault] FAULT_QDRANT=down：向量召回不可用，降级为仅 BM25")
    return True
