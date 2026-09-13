"""RAGAS 评估脚本：对 RAG 链路跑 faithfulness / answer_relevancy / context_precision / context_recall。

用法（在 backend/ 下）：
    venv\\Scripts\\python.exe scripts/run_eval.py [数据集路径] [--dump-json 路径] [--judge-retries N]

- 数据集路径缺省为 backend/app/data/eval_mall_qa.json，也可传入自定义 JSON
  （字段：question / ground_truth / adversarial），便于跑部分样本做冒烟验证
- --dump-json：把逐条全量明细（问题 / 命中文档 / 检索片段全文 / 回答 /
  ground_truth / 四项分数 / 失败原因）写入指定 JSON。报告里的回答会被截断到
  60 字，人工抽检必须依赖这个文件，不要只看 stdout
- --judge-retries：首轮判分出现 NaN 的行逐条重试的次数（默认 2，0 = 关闭）。
  ragas 的 raise_exceptions=False 会把失败行静默记成 NaN，只报"有效 N/M"，
  重试后仍失败的行会带失败原因写进 --dump-json 的 row_errors，并在报告末尾列出
- 限速保护（环境变量）：判分按 EVAL_JUDGE_BATCH_SIZE（默认 3）分批，
  批间停 EVAL_JUDGE_BATCH_PAUSE_S（默认 25 秒），重试前也等一个批间歇；
  生成阶段条间停 EVAL_GEN_PAUSE_S（默认 3 秒）。供应商网关（商汤等）
  RPM/TPM 远严于直连，不分批会被 429 打穿（实测 12 rpm 间隔稳定通过）
- judge 输出上限：EVAL_JUDGE_MAX_TOKENS（默认 8192，4096 下长 JSON 会被截断）

流程：
1. 读取 backend/app/data/eval_mall_qa.json（字段：question / ground_truth / adversarial），
   校验每条含非空 question / ground_truth，非法条目跳过并打印 warning
2. 逐条跑 RAG 链路：复用 app.core.rag.engine 的 retrieve() + generate()，
   得到检索片段（contexts）与模型回答（answer），不重复实现检索/生成逻辑
   （说明：未走 engine.answer() 的 CRAG rewrite_retry 分支，直接使用主链路
    retrieve → generate，以便拿到完整检索片段供评估）
3. 用 ragas 0.4 评估 4 项指标（评分 LLM 走项目配置 DeepSeek/Ollama，embedding 复用项目 bge 模型）
4. 输出逐条明细（问题 / 命中文档 / 四项分数）+ 末尾平均分

依赖外部服务：LLM（DeepSeek 主 / Ollama 备，走 app 配置）、Qdrant（可选，失败降级仅 BM25）、
本地 embedding / reranker 模型。LLM 不可用、知识来源完全不可用或评估未产生任何有效得分时，
打印明确错误并以非 0 退出码退出，不假装成功。

退出码：
  0  评估完成（含部分 NaN 行）
  1  数据集缺失 / 格式错误 / 无有效条目
  2  LLM 不可用（未配置 API Key 或 DeepSeek/Ollama 均失败）
  3  知识来源完全不可用（Qdrant 不可达且本地 knowledge/mall 无文档）
  4  评估未产生任何有效得分（评分调用全部失败）
  5  ragas 不可导入（评估依赖缺失，如环境未按锁安装 pillow）

注意：ragas 0.4.3 硬依赖 langchain_community.chat_models.vertexai，
而 langchain-community 0.4.x 已移除该模块，import ragas 前必须安装兼容垫片（见 _install_vertexai_shim）。
"""

from __future__ import annotations

import asyncio
import glob
import inspect
import json
import logging
import math
import os
import sys
import time
import types
import warnings

# ========== ragas 兼容垫片（必须在 import ragas 之前执行） ==========


def _install_vertexai_shim() -> None:
    """langchain-community 0.4.x 移除了 chat_models.vertexai，ragas 0.4.3 仍硬 import。

    ChatVertexAI 只出现在 ragas 的 MULTIPLE_COMPLETION_SUPPORTED 名单里
    （本脚本用不到），注入空模块垫片即可让 ragas 正常导入。
    """
    if "langchain_community.chat_models.vertexai" in sys.modules:
        return
    shim = types.ModuleType("langchain_community.chat_models.vertexai")

    class ChatVertexAI:  # 仅占位，不会被实例化
        pass

    shim.ChatVertexAI = ChatVertexAI
    sys.modules["langchain_community.chat_models.vertexai"] = shim


_install_vertexai_shim()

# 关闭 ragas 遥测；屏蔽 aevaluate / langchain-community 弃用告警，保持输出干净
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
warnings.filterwarnings("ignore", category=DeprecationWarning)

import openai  # noqa: E402

try:
    from ragas import aevaluate  # noqa: E402
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample  # noqa: E402
    from ragas.embeddings.base import BaseRagasEmbedding, BaseRagasEmbeddings  # noqa: E402
    from ragas.llms import llm_factory  # noqa: E402
    from ragas.metrics import (  # noqa: E402
        context_precision,
        context_recall,
        faithfulness,
    )
    from ragas.metrics.collections import (
        AnswerRelevancy as CollectionsAnswerRelevancy,  # noqa: E402
    )
    from ragas.metrics.collections.answer_relevancy.util import (  # noqa: E402
        AnswerRelevanceInput,
        AnswerRelevanceOutput,
        AnswerRelevancePrompt,
    )
    RAGAS_AVAILABLE = True
    RAGAS_IMPORT_ERROR: BaseException | None = None
except ImportError as e:
    # ragas 缺失或其传递依赖不可用时降级：本模块仍可正常导入（帮助函数单测
    # /纯逻辑用例不被连坐——CI 曾因 ragas→PIL 导入失败在 pytest 收集阶段
    # 整体 ERROR），评估入口（main/run_evaluation）显式报错退出，不裸抛栈
    aevaluate = None
    EvaluationDataset = None  # type: ignore[assignment,misc]
    SingleTurnSample = None  # type: ignore[assignment,misc]
    BaseRagasEmbedding = None  # type: ignore[assignment,misc]
    BaseRagasEmbeddings = None  # type: ignore[assignment,misc]
    llm_factory = None  # type: ignore[assignment]
    context_precision = None  # type: ignore[assignment,misc]
    context_recall = None  # type: ignore[assignment,misc]
    faithfulness = None  # type: ignore[assignment,misc]
    CollectionsAnswerRelevancy = None  # type: ignore[assignment,misc]
    AnswerRelevanceInput = None  # type: ignore[assignment,misc]
    AnswerRelevanceOutput = None  # type: ignore[assignment,misc]
    AnswerRelevancePrompt = None  # type: ignore[assignment,misc]
    RAGAS_AVAILABLE = False
    RAGAS_IMPORT_ERROR = e

from app.config import get_settings  # noqa: E402
from app.core.infra.llm_factory import LLMUnavailableError, call_llm  # noqa: E402
from app.core.infra.qdrant import get_qdrant  # noqa: E402
from app.core.rag import engine  # noqa: E402
from app.core.rag.bm25 import get_bm25  # noqa: E402
from app.core.rag.chunking import chunk_text  # noqa: E402
from app.core.rag.embedding import embed_sync  # noqa: E402

logger = logging.getLogger(__name__)
settings = get_settings()

# backend/scripts/run_eval.py → backend/app/data/eval_mall_qa.json
DATASET_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "data", "eval_mall_qa.json")
# backend/scripts/run_eval.py → 仓库根 knowledge/mall（Qdrant 不可用时的 BM25 兜底来源）
KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "knowledge", "mall")

# RAGAS 指标（aevaluate 批处理部分）。answer_relevancy 单独走 collections 新版
# （循环采样 3 个反向问题取均值，不依赖 LLM 的 n 参数；旧版在 Instructor LLM 下
# 会退化为单点采样，见 run_evaluation 注释）。
# ragas 不可导入时为空列表（_select_metrics 返回空，评估入口在 main 显式报错退出）
EVAL_METRICS = [
    m for m in (faithfulness, context_precision, context_recall) if m is not None
]


def _is_missing(value) -> bool:
    """分数缺失判定：None 或 NaN（ragas raise_exceptions=False 时失败行记为 NaN）。"""
    return value is None or (isinstance(value, float) and math.isnan(value))


def _split_batches(total: int, batch_size: int) -> list[tuple[int, int]]:
    """把 [0, total) 切成若干 [start, end) 批次（判分限速用，可单测）。"""
    size = max(1, batch_size)
    return [(s, min(s + size, total)) for s in range(0, total, size)]


class _ScoreBag:
    """判分结果容器：逐批收集后统一成 {scores: [...]}，下游（重试/报告/落盘）不感知分批。"""

    def __init__(self, scores: list[dict]) -> None:
        self.scores = scores


# ---------- judge 调用旁路记录（诊断 IncompleteOutputException 用） ----------

_JUDGE_CALL_SINK: list[dict] = []
_JUDGE_CAPTURE = False


class _CaptureCompletions:
    """拦截 chat.completions.create，记录 finish_reason / 输出长度 / usage / 异常。

    ragas 把判分异常吞成 NaN，row_errors 只剩"解析失败"，看不出截断诱因。
    这里在 HTTP 客户端层旁路记账：finish_reason=length 即实锤输出截断，
    配合 prompt_tokens 还能看出是不是长输入触发（同步与异步 client 都要包）。
    """

    def __init__(self, inner, sink: list, label: str) -> None:
        self._inner = inner
        self._sink = sink
        self._label = label

    def _record(self, kwargs: dict, response=None, error: Exception | None = None) -> None:
        try:
            messages = kwargs.get("messages") or []
            entry: dict = {
                "ts": time.strftime("%H:%M:%S"),
                "label": self._label,
                "model": kwargs.get("model"),
                "max_tokens": kwargs.get("max_tokens"),
                "input_chars": sum(
                    len(str(m.get("content") or ""))
                    for m in messages
                    if isinstance(m, dict)
                ),
            }
            if error is not None:
                entry["error"] = f"{type(error).__name__}: {error}"
            if response is not None:
                choices = getattr(response, "choices", None) or []
                choice = choices[0] if choices else None
                content = getattr(getattr(choice, "message", None), "content", "") or ""
                entry["finish_reason"] = getattr(choice, "finish_reason", None)
                entry["output_chars"] = len(content)
                entry["output_tail"] = content[-80:]
                usage = getattr(response, "usage", None)
                if usage is not None:
                    entry["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
                    entry["completion_tokens"] = getattr(usage, "completion_tokens", None)
            self._sink.append(entry)
        except Exception:
            pass  # 记账失败绝不能影响评估本身

    def create(self, *args, **kwargs):
        try:
            result = self._inner.create(*args, **kwargs)
        except Exception as e:  # 调用本身就失败（网络/超时/限流）也要记账
            self._record(kwargs, error=e)
            raise
        if inspect.isawaitable(result):

            async def _await_and_record():
                try:
                    resp = await result
                except Exception as e:
                    self._record(kwargs, error=e)
                    raise
                self._record(kwargs, response=resp)
                return resp

            return _await_and_record()
        self._record(kwargs, response=result)
        return result


class _CaptureChat:
    def __init__(self, inner_chat, sink: list, label: str) -> None:
        self.completions = _CaptureCompletions(inner_chat.completions, sink, label)
        self._inner_chat = inner_chat

    def __getattr__(self, name):
        return getattr(self._inner_chat, name)


class _CaptureClient:
    def __init__(self, inner, sink: list, label: str) -> None:
        self.chat = _CaptureChat(inner.chat, sink, label)
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _judge_calls_summary() -> str:
    """finish_reason 分布 + 异常类型分布（报告用一句话说清截断情况）。"""
    if not _JUDGE_CALL_SINK:
        return "无 judge 调用记录"
    by_finish: dict[str, int] = {}
    by_error: dict[str, int] = {}
    for e in _JUDGE_CALL_SINK:
        if e.get("error"):
            etype = e["error"].split(":", 1)[0]
            by_error[etype] = by_error.get(etype, 0) + 1
        else:
            fr = str(e.get("finish_reason"))
            by_finish[fr] = by_finish.get(fr, 0) + 1
    parts = [f"共 {len(_JUDGE_CALL_SINK)} 次调用"]
    if by_finish:
        parts.append("finish_reason=" + json.dumps(by_finish, ensure_ascii=False))
    if by_error:
        parts.append("异常=" + json.dumps(by_error, ensure_ascii=False))
    return "；".join(parts)


def _write_judge_calls(dump_path: str | None) -> None:
    """judge 调用记录落盘（sidecar JSONL）+ 打印分布摘要。"""
    if not _JUDGE_CALL_SINK:
        return
    logger.info("[Eval] judge 调用记录：%s", _judge_calls_summary())
    if dump_path:
        sidecar = dump_path + ".judge_calls.jsonl"
        with open(sidecar, "w", encoding="utf-8") as f:
            for e in _JUDGE_CALL_SINK:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        logger.info("[Eval] judge 原始调用记录已落盘: %s", os.path.abspath(sidecar))


def load_dataset(path: str) -> list[dict]:
    """加载评估数据集，校验每条含非空 question；ground_truth 可空。

    - question 为空 → 跳过（无效条目）
    - ground_truth 为空 → 保留：这类条目来自 feedback bad case 回流
      （export_feedback_badcases.py，无标准答案），评估时自动剔除依赖
      reference 的 context_recall（见 _select_metrics）
    非法条目跳过并打印 warning；无任何有效条目时退出码 1。
    """
    if not os.path.exists(path):
        logger.error("[Eval] 数据集不存在: %s", path)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        logger.error("[Eval] 数据集格式错误：应为 JSON 数组")
        sys.exit(1)

    valid: list[dict] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            logger.warning("[Eval] 跳过非法条目 #%d：不是对象", i)
            continue
        question = (item.get("question") or "").strip()
        if not question:
            logger.warning(
                "[Eval] 跳过非法条目 #%d：question 不能为空", i
            )
            continue
        ground_truth = (item.get("ground_truth") or "").strip()
        valid.append(
            {
                "question": question,
                "ground_truth": ground_truth,
                "adversarial": bool(item.get("adversarial", False)),
            }
        )

    no_gt = sum(1 for v in valid if not v["ground_truth"])
    logger.info(
        "[Eval] 数据集加载完成：%d 条有效（含 %d 条对抗样本，%d 条无 ground_truth），跳过 %d 条",
        len(valid),
        sum(1 for v in valid if v["adversarial"]),
        no_gt,
        len(data) - len(valid),
    )
    if not valid:
        logger.error("[Eval] 数据集没有任何有效条目，无法评估")
        sys.exit(1)
    return valid


def _select_metrics(rows: list[dict]) -> list:
    """根据样本集选择可评估的指标。

    存在无 ground_truth 的样本（feedback bad case 回流）时剔除
    context_recall——该指标强依赖 reference，无标准答案时得分无意义。
    """
    if rows and any(not (r.get("ground_truth") or "").strip() for r in rows):
        if context_recall in EVAL_METRICS:
            logger.warning(
                "[Eval] 存在无 ground_truth 样本，本次评估跳过 context_recall（依赖标准答案）"
            )
        return [m for m in EVAL_METRICS if m != context_recall]
    return list(EVAL_METRICS)


def _load_knowledge_docs() -> list[dict]:
    """读取 knowledge/mall/*.md 并分块（与 scripts/seed_mall_kb.py 一致）。"""
    docs: list[dict] = []
    for path in sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md"))):
        doc_id = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            text = f.read()
        chunks = chunk_text(
            text,
            metadata={
                "doc_id": doc_id,
                "title": doc_id,
                "source": f"knowledge/mall/{doc_id}.md",
                "category": "mall",
                "security_group": ["user", "agent", "admin"],
            },
        )
        docs.extend(chunks)
    return docs


async def build_bm25_index() -> tuple[bool, int]:
    """构建 BM25 内存索引（与 app/main.py 启动逻辑一致）。

    - Qdrant 可用：从全量向量库 scroll_all 构建（与线上一致）
    - Qdrant 不可用：降级从 knowledge/mall 源文档构建（BM25 一等公民兜底）

    Returns:
        (qdrant_ok, 索引文档数)
    """
    qdrant = get_qdrant()
    await qdrant.connect()

    if qdrant.is_connected:
        all_docs = await qdrant.scroll_all()
        get_bm25().build(all_docs)
        logger.info("[Eval] Qdrant 连接成功，BM25 索引构建 %d 文档", len(all_docs))
        return True, len(all_docs)

    # Qdrant 不可用：用源文档构建本地 BM25（降级，仍可评估）
    logger.warning("[Eval] Qdrant 不可用，降级为仅 BM25（从 knowledge/mall 源文档构建索引）")
    docs = _load_knowledge_docs()
    get_bm25().build(docs)
    logger.info("[Eval] BM25 索引构建 %d 文档（源文档降级模式）", len(docs))
    return False, len(docs)


async def probe_llm() -> None:
    """探测 LLM 可用性（主供应商 → Ollama 降级链），不可用则报错退出（退出码 2）。"""
    if settings.LLM_PROVIDER != "ollama":
        api_key, _, _ = settings.llm_profile()
        if not api_key:
            logger.error(
                "[Eval] 未配置 %s 的 API key（LLM_PROVIDER=%s），"
                "无法生成回答与 ragas 评分，评估终止",
                settings.LLM_PROVIDER.upper(),
                settings.LLM_PROVIDER,
            )
            sys.exit(2)
    try:
        reply = await call_llm("请只回复两个字：正常", system="你是连通性测试助手。")
        logger.info("[Eval] LLM 连通性探测通过（回复: %s）", reply.strip()[:20])
    except LLMUnavailableError:
        logger.error("[Eval] LLM 不可用（主供应商与 Ollama 均失败），评估无法继续")
        sys.exit(2)


async def run_rag(item: dict) -> dict:
    """跑一遍 RAG 链路：engine.retrieve（检索）→ engine.generate（生成）。

    复用 app.core.rag.engine 现有函数，不重复实现检索/生成逻辑。
    """
    question = item["question"]
    retrieval = await engine.retrieve(question, role="user")
    contexts = retrieval["contexts"]

    gen = await engine.generate(question, contexts)

    hit_docs: list[str] = []
    for c in contexts:
        source = c.get("source") or c.get("doc_id") or ""
        if source and source not in hit_docs:
            hit_docs.append(source)

    # 检索片段明细：人工抽检要知道"判分时到底喂了哪些片段"，
    # 只留文本无法回答"正确内容有没有进上下文"（见 RAGAS 抽检记录 2026-09-13）
    contexts_detail: list[dict] = []
    for c in contexts:
        text = c.get("text", "") or ""
        detail = {
            "source": c.get("source") or c.get("doc_id") or "",
            "doc_id": c.get("doc_id", ""),
            "chars": len(text),
        }
        for score_key in ("score", "rerank_score", "rrf_score"):
            if c.get(score_key) is not None:
                detail[score_key] = c[score_key]
        contexts_detail.append(detail)

    return {
        "question": question,
        "ground_truth": item["ground_truth"],
        "adversarial": item["adversarial"],
        "contexts": [c.get("text", "") for c in contexts],
        "contexts_detail": contexts_detail,
        "hit_docs": hit_docs,
        "answer": gen["answer"],
        "degraded": retrieval["degraded"] + (["llm"] if gen["degraded"] else []),
    }


if RAGAS_AVAILABLE:

    class ProjectEmbeddings(BaseRagasEmbeddings, BaseRagasEmbedding):
        """ragas 评估用 embedding：复用项目 embedding（BAAI/bge-base-zh-v1.5）。

        同时继承新旧两套基类：
        - 旧版指标（aevaluate 批处理）用 embed_query/embed_documents
        - collections 新版指标用 embed_text/aembed_text，且构造时做
          isinstance(BaseRagasEmbedding) 校验，因此必须继承新版基类
        两套接口转发到同一实现（embed_sync）。
        embedding 失败时抛出异常，由 ragas 记为该行 NaN（不会静默给出错误分数）。
        """

        def embed_query(self, text: str) -> list[float]:
            vecs = embed_sync([text])
            if not vecs:
                raise RuntimeError("项目 embedding 不可用（embed_sync 返回 None）")
            return vecs[0]

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            vecs = embed_sync(list(texts))
            if vecs is None:
                raise RuntimeError("项目 embedding 不可用（embed_sync 返回 None）")
            return vecs

        async def aembed_query(self, text: str) -> list[float]:
            return self.embed_query(text)

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return self.embed_documents(texts)

        # ---- collections 新版指标接口（ragas.metrics.collections）----
        def embed_text(self, text: str, **kwargs) -> list[float]:
            return self.embed_query(text)

        async def aembed_text(self, text: str, **kwargs) -> list[float]:
            return self.embed_query(text)

        async def aembed_texts(self, texts: list[str], **kwargs) -> list[list[float]]:
            return self.embed_documents(list(texts))

else:
    # ragas 不可导入时的占位绑定：仅 run_evaluation（已前置拦截）会引用
    ProjectEmbeddings = None  # type: ignore[assignment,misc]


def build_ragas_llm(async_client: bool = False, max_tokens: int | None = None):
    """构造 ragas 评分 LLM：与项目配置一致（LLM_PROVIDER=deepseek → DeepSeek，
    LLM_PROVIDER=ollama → Ollama），OpenAI 兼容协议 + instructor 结构化输出。

    - async_client=False（默认）：同步 OpenAI client，供 aevaluate 批处理使用
      （同步路径并发调用更稳，Ollama 排队时不易触发 90s 超时）
    - async_client=True：AsyncOpenAI，供 collections 版指标使用（ascore 内部
      调用 llm.agenerate，同步 client 会报
      "Cannot use agenerate() with a synchronous client"）
    - 凭据走 settings.llm_profile() 多供应商解析（deepseek/sensenova/...），
      不能直接读 DEEPSEEK_*，否则 LLM_PROVIDER 切到其他供应商时评估仍打直连
    - max_tokens：judge 输出上限。2026-09-13 端到端复查发现 4096 下
      deepseek-v4-flash 的长 JSON 结构化输出被截断 → IncompleteOutputException，
      42 条里 15 行 faithfulness 静默丢失且重试无法挽回（同输入重复截断），
      故默认提到 8192，可用环境变量 EVAL_JUDGE_MAX_TOKENS / CLI 覆盖

    注意：ragas 指标调用是长 JSON 结构化输出，本地 Ollama 推理较慢，
    客户端超时放宽到 90s（离线批量评估，不走交互链路的 15s 降级超时）。
    """
    if max_tokens is None:
        try:
            max_tokens = int(os.environ.get("EVAL_JUDGE_MAX_TOKENS", "8192"))
        except ValueError:
            max_tokens = 8192
    client_cls = openai.AsyncOpenAI if async_client else openai.OpenAI
    if settings.LLM_PROVIDER == "ollama":
        client = client_cls(
            api_key="ollama",
            base_url=settings.OLLAMA_BASE_URL + "/v1",
            timeout=90,
        )
        model = settings.OLLAMA_MODEL
    else:
        api_key, base_url, model = settings.llm_profile()
        client = client_cls(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.LLM_TIMEOUT,
        )
    if _JUDGE_CAPTURE:
        raw_client = client
        client = _CaptureClient(client, _JUDGE_CALL_SINK, "async" if async_client else "sync")
    try:
        return llm_factory(
            model,
            client=client,
            max_tokens=max_tokens,
        )
    except Exception as e:
        if _JUDGE_CAPTURE:
            logger.warning("[Eval] judge 调用记录包装与 instructor 不兼容（%s），退回原始 client", e)
            return llm_factory(model, client=raw_client, max_tokens=max_tokens)
        raise


if RAGAS_AVAILABLE:

    class ZhAnswerRelevancePrompt(AnswerRelevancePrompt):
        """反向问题生成 prompt：强制与回答同语言（中文）。

        默认英文指令在 DeepSeek 下会随机生成英文反向问题，跨语言 embedding
        相似度极低 → answer_relevancy 被随机拉低（0-3 个英文问题，样本级
        ±0.2-0.7 波动，甚至 noncommittal 判定后的 0.000 极端值）。中文答案
        必须生成中文问题，跨语言对比无意义。
        """

        instruction = """Generate a question for the given answer and identify if the answer is noncommittal.
The question MUST be written in the SAME LANGUAGE as the answer (if the answer is Chinese, write the question in Chinese).
Give noncommittal as 1 if the answer is noncommittal (evasive, vague, or ambiguous) and 0 if the answer is substantive.
Examples of noncommittal answers: "I don't know", "I'm not sure", "It depends"."""

        examples = [
            (
                AnswerRelevanceInput(response="华为 Mate 70 Pro 售价 6999 元。"),
                AnswerRelevanceOutput(question="华为 Mate 70 Pro 多少钱？", noncommittal=0),
            ),
            (
                AnswerRelevanceInput(
                    response="我无法回答这个问题，因为我没有相关信息。"
                ),
                AnswerRelevanceOutput(question="这个问题有答案吗？", noncommittal=1),
            ),
        ]

    class ZhAnswerRelevancy(CollectionsAnswerRelevancy):
        """collections AnswerRelevancy + 中文反向问题约束（见 ZhAnswerRelevancePrompt）。"""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.prompt = ZhAnswerRelevancePrompt()

else:
    # ragas 不可导入时的占位绑定：仅 run_evaluation（已前置拦截）会引用
    ZhAnswerRelevancePrompt = None  # type: ignore[assignment,misc]
    ZhAnswerRelevancy = None  # type: ignore[assignment,misc]


async def _retry_missing_scores(
    rows: list[dict],
    samples: list,
    metrics: list,
    result,
    ragas_llm,
    embeddings,
    retries: int,
    pause_s: int = 0,
) -> dict:
    """对首轮判分缺分的行逐条重试，返回诊断信息。

    ragas 的 raise_exceptions=False 会把失败行记成 NaN 且不给出原因（报告只显示
    "有效 30/35"）。这里对缺失行单独重跑（每次只送一条样本），把能救回来的分数
    补上并记录重试轮数；重试后仍缺失的行连问题、缺失指标一起写进 row_errors，
    由 print_report 列出、由 --dump-json 落盘。
    """
    names = [m.name for m in metrics]
    diag: dict = {"retry_rounds": 0, "retried_rows": 0, "row_errors": []}
    for attempt in range(1, retries + 1):
        pending = [
            i
            for i, s in enumerate(result.scores)
            if any(_is_missing(s.get(n)) for n in names)
        ]
        if not pending:
            break
        diag["retry_rounds"] = attempt
        diag["retried_rows"] += len(pending)
        logger.warning(
            "[Eval] 判分缺失重试第 %d/%d 轮：第 %s 行",
            attempt,
            retries,
            [i + 1 for i in pending],
        )
        if pause_s:
            # 429/限流导致的失败，立刻重试会再次撞墙，先等一个批间歇
            await asyncio.sleep(pause_s)
        sub_dataset = EvaluationDataset(samples=[samples[i] for i in pending])
        try:
            sub_result = await aevaluate(
                dataset=sub_dataset,
                metrics=metrics,
                llm=ragas_llm,
                embeddings=embeddings,
                raise_exceptions=False,
                show_progress=False,
            )
        except Exception as e:  # 重试批次整体失败：保留原始缺失，进入下一轮
            logger.warning("[Eval] 重试批次异常（保留原始缺失）: %s", e)
            continue
        if len(sub_result.scores) != len(pending):
            # zip 按位置配对，行数不一致会把 A 行的分数写到 B 行上（静默错位），
            # 宁可保持缺失也不能写错分数
            logger.warning(
                "[Eval] 重试返回行数不匹配（请求 %d 行，返回 %d 行），本轮跳过合并",
                len(pending),
                len(sub_result.scores),
            )
            continue
        for idx, sub_scores in zip(pending, sub_result.scores):
            for name in names:
                if _is_missing(result.scores[idx].get(name)) and not _is_missing(
                    sub_scores.get(name)
                ):
                    result.scores[idx][name] = sub_scores[name]
    return diag


def _collect_row_errors(
    rows: list[dict],
    scores: list[dict],
    metric_names: list[str],
    ar_errors: dict[int, str],
    retry_rounds: int,
) -> list[dict]:
    """汇总仍缺分的行（问题 / 缺失指标 / 原因），供报告与 --dump-json 暴露。

    单独拆出来是为了能离线单测（不依赖 ragas 与 LLM）。
    """
    row_errors: list[dict] = []
    for i, score_row in enumerate(scores):
        missing = [n for n in metric_names if _is_missing(score_row.get(n))]
        if not missing:
            continue
        reasons = []
        if i in ar_errors:
            reasons.append(f"answer_relevancy: {ar_errors[i]}")
        batch_failed = [n for n in missing if n != "answer_relevancy"]
        if batch_failed:
            reasons.append(
                f"{'/'.join(batch_failed)}: judge 调用或结果解析失败，"
                f"重试 {retry_rounds} 轮后仍未产出分数"
            )
        row_errors.append(
            {
                "index": i + 1,
                "question": rows[i]["question"],
                "adversarial": bool(rows[i].get("adversarial")),
                "missing_metrics": missing,
                "reason": "；".join(reasons) or "未知原因",
            }
        )
    return row_errors


async def run_evaluation(rows: list[dict], judge_retries: int = 2, judge_max_tokens: int | None = None):
    """构造 ragas 数据集并执行评估。

    - faithfulness / context_precision / context_recall：aevaluate 批处理
    - answer_relevancy：单独用 collections 新版 AnswerRelevancy 逐条 ascore——
      它在内部循环 strictness 次独立生成反向问题（不依赖 LLM 的 n 参数）。
      旧版（ragas.metrics.answer_relevancy）在 Instructor LLM 下只调用一次、
      退化为单点采样（Ollama 等不支持 n>1 的端点），分数是单次余弦相似度，
      方差大；collections 版为 3 问题均值，评估口径更可靠。

    首批判分后对缺分行逐条重试（judge_retries 轮，默认 2）；重试后仍缺分的行
    记入诊断信息 row_errors（含问题、缺失指标、原因），由报告与 --dump-json 暴露，
    不再只显示一句"有效 N/M 行"。
    """
    if not RAGAS_AVAILABLE:  # pragma: no cover - main 已前置拦截，防御性兜底
        raise RuntimeError(
            f"ragas 不可导入，无法执行评估（RAGAS_IMPORT_ERROR: {RAGAS_IMPORT_ERROR}）"
        )
    metrics = _select_metrics(rows)
    samples = [
        SingleTurnSample(
            user_input=r["question"],
            retrieved_contexts=r["contexts"],
            response=r["answer"],
            reference=(r["ground_truth"] or None),  # 无标准答案（bad case 回流）时传 None
        )
        for r in rows
    ]

    ragas_llm = build_ragas_llm(max_tokens=judge_max_tokens)
    embeddings = ProjectEmbeddings()

    if settings.LLM_PROVIDER == "ollama":
        active_model = settings.OLLAMA_MODEL
    else:
        _, _, active_model = settings.llm_profile()
    logger.info(
        "[Eval] 开始 ragas 评估：%d 条样本 × %d 项指标 + answer_relevancy(3 问题均值)（评分 LLM: %s / %s）",
        len(samples),
        len(metrics),
        settings.LLM_PROVIDER,
        active_model,
    )

    # 分批判分 + 批间歇：供应商网关（如商汤）的 RPM/TPM 限制远严于直连，
    # 42 条一次性并发会被 429 打穿（2026-09-13 实测 rpm exhausted，全部判分失败）。
    # 实测该网关 5 秒间隔（12 rpm）稳定通过，故默认每批 3 条（≈9 个并发调用）
    # + 批间 25 秒，把突发压到 12-18 rpm 以内；额度更紧时调小 batch / 调大 pause。
    batch_size = max(1, int(os.environ.get("EVAL_JUDGE_BATCH_SIZE", "3")))
    batch_pause = max(0, int(os.environ.get("EVAL_JUDGE_BATCH_PAUSE_S", "25")))
    ranges = _split_batches(len(samples), batch_size)
    batch_scores: list[dict] = []
    for bi, (start, end) in enumerate(ranges, 1):
        logger.info("[Eval] 判分批次 %d/%d：样本 %d-%d", bi, len(ranges), start + 1, end)
        sub_result = await aevaluate(
            dataset=EvaluationDataset(samples=samples[start:end]),
            metrics=metrics,
            llm=ragas_llm,
            embeddings=embeddings,
            raise_exceptions=False,
            show_progress=True,
        )
        for row in sub_result.scores:
            batch_scores.append(dict(row))
        # 行数对齐保护：少返回就补空行，避免后续 zip 错位（比缺分更糟）
        while len(batch_scores) < end:
            batch_scores.append({})
        if bi < len(ranges) and batch_pause:
            logger.info("[Eval] 批次间歇 %ds（限速保护）", batch_pause)
            await asyncio.sleep(batch_pause)
    result = _ScoreBag(batch_scores)

    # 缺失行重试：把能救回来的分数补上，救不回来的记录原因（P0-2）
    diag = await _retry_missing_scores(
        rows, samples, metrics, result, ragas_llm, embeddings, judge_retries, pause_s=batch_pause
    )
    if diag["retry_rounds"]:
        logger.info(
            "[Eval] 判分缺失重试完成：%d 轮，累计重试 %d 行",
            diag["retry_rounds"],
            diag["retried_rows"],
        )

    # answer_relevancy：collections 版逐条循环采样（strictness=3）；
    # 需要 AsyncOpenAI client（ascore 内部调 llm.agenerate）
    ar_metric = ZhAnswerRelevancy(
        llm=build_ragas_llm(async_client=True, max_tokens=judge_max_tokens),
        embeddings=embeddings,
        strictness=3,
    )
    ar_errors: dict[int, str] = {}
    for i, (row, score_row) in enumerate(zip(rows, result.scores)):
        # 与 aevaluate 的 raise_exceptions=False 语义一致：失败记 NaN，不中断
        try:
            if not row["answer"]:
                raise ValueError("response 为空（RAG 链路失败行）")
            metric_result = await ar_metric.ascore(
                user_input=row["question"], response=row["answer"]
            )
            score_row["answer_relevancy"] = metric_result.value
        except Exception as e:
            logger.warning("[Eval] answer_relevancy 评分失败: %s", e)
            score_row["answer_relevancy"] = float("nan")
            ar_errors[i] = f"{type(e).__name__}: {e}"

    diag["answer_relevancy_errors"] = ar_errors
    names = [m.name for m in metrics] + ["answer_relevancy"]
    diag["row_errors"] = _collect_row_errors(
        rows, result.scores, names, ar_errors, diag["retry_rounds"]
    )

    return result, diag


def _fmt(score: float) -> str:
    """分数格式化：NaN 显示为 '-'。"""
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return "-"
    return f"{score:.3f}"


class _MultiGenDegradationCapture(logging.Handler):
    """捕获 ragas 的多生成退化日志。

    ragas 的 answer_relevancy 默认从回答反向生成 3 个问题取相似度均值；
    LLM 不支持 n>1（如 Ollama 的 OpenAI 兼容端点忽略 n 参数）时 ragas
    退化为单问题采样，单点相似度波动大。统计退化次数用于报告标注。
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "returned 1 generations" in record.getMessage():
            self.count += 1


def print_report(rows: list[dict], scores: list[dict], diag: dict | None = None) -> int:
    """输出逐条明细 + 平均分 + 判分失败行清单。返回全部 NaN 的行数（全失败时调用方退出非 0）。"""
    # 打印项 = 本次实际评估的指标（无 ground_truth 时不含 context_recall）+ collections 版 answer_relevancy
    names = [m.name for m in _select_metrics(rows)] + ["answer_relevancy"]

    print("\n===== RAGAS 逐条评估明细 =====")
    all_nan_rows = 0
    for i, (row, s) in enumerate(zip(rows, scores), 1):
        tag = "对抗" if row["adversarial"] else "常规"
        question = row["question"]
        if len(question) > 34:
            question = question[:34] + "…"
        hit = "、".join(row["hit_docs"]) if row["hit_docs"] else "无命中（contexts 为空）"
        answer = row["answer"]
        if len(answer) > 60:
            answer = answer[:60] + "…"
        print(f"[{i:02d}] ({tag}) {question}")
        print(f"     命中: {hit}")
        print(f"     回答: {answer}")
        print(
            "     得分: "
            + "  ".join(f"{name}={_fmt(s.get(name, float('nan')))}" for name in names)
        )
        if all(
            s.get(name) is None
            or (isinstance(s.get(name), float) and math.isnan(s.get(name)))
            for name in names
        ):
            all_nan_rows += 1

    print("\n===== RAGAS 平均分 =====")
    print(f"样本数: {len(rows)}（对抗样本 {sum(1 for r in rows if r['adversarial'])} 条）")
    print("answer_relevancy 为 3 问题均值（collections 循环采样，非单点采样）")
    for name in names:
        values = [
            s[name]
            for s in scores
            if s.get(name) is not None
            and not (isinstance(s.get(name), float) and math.isnan(s.get(name)))
        ]
        if values:
            avg = sum(values) / len(values)
            std = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5
            print(f"  {name:<20} = {avg:.3f} ± {std:.3f}（有效 {len(values)}/{len(rows)} 行）")
        else:
            print(f"  {name:<20} = N/A（该指标全部失败）")

    # 常规 / 对抗分组统计：对抗样本设计为低分，混在一起算平均会误导质量判断
    for group_name, flag in (("常规样本", False), ("对抗样本", True)):
        group_scores = [
            s for s, r in zip(scores, rows) if bool(r["adversarial"]) == flag
        ]
        if not group_scores:
            continue
        print(f"\n--- {group_name}（{len(group_scores)} 条）---")
        for name in names:
            values = [
                s[name]
                for s in group_scores
                if s.get(name) is not None
                and not (isinstance(s.get(name), float) and math.isnan(s.get(name)))
            ]
            if values:
                avg = sum(values) / len(values)
                std = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5
                print(f"  {name:<20} = {avg:.3f} ± {std:.3f}（有效 {len(values)}/{len(group_scores)} 行）")
            else:
                print(f"  {name:<20} = N/A")

    # 判分失败行：不再只报"有效 N/M"，把失效的行和原因列出来（P0-2）
    row_errors = (diag or {}).get("row_errors") or []
    if row_errors:
        print(f"\n===== 判分失败行（{len(row_errors)} 条，需人工复查）=====")
        if (diag or {}).get("retry_rounds"):
            print(f"已按缺失行重试 {diag['retry_rounds']} 轮，以下行重试后仍缺分：")
        for err in row_errors:
            tag = "对抗" if err.get("adversarial") else "常规"
            question = err.get("question", "")
            if len(question) > 34:
                question = question[:34] + "…"
            print(
                f"  [{err.get('index', 0):02d}] ({tag}) 缺 {','.join(err.get('missing_metrics', []))}"
                f" | {question} | {err.get('reason', '')}"
            )
    elif diag is not None:
        print("\n判分完整性：全部样本均有分数（无失败行）")

    return all_nan_rows


def dump_json(
    path: str,
    dataset_path: str,
    rows: list[dict],
    scores: list[dict],
    diag: dict | None = None,
) -> None:
    """把逐条全量明细落盘，供人工抽检复查（回答不再被截断到 60 字）。

    记录内容：问题 / 是否对抗 / ground_truth / 命中文档 / 检索片段明细与全文 /
    回答 / 降级标记 / 四项分数（缺失记 null）/ 失败原因。
    """
    names = [m.name for m in _select_metrics(rows)] + ["answer_relevancy"]
    records = []
    for i, (row, s) in enumerate(zip(rows, scores), 1):
        records.append(
            {
                "index": i,
                "question": row["question"],
                "adversarial": bool(row.get("adversarial")),
                "ground_truth": row.get("ground_truth", ""),
                "hit_docs": row.get("hit_docs", []),
                "contexts_detail": row.get("contexts_detail", []),
                "contexts_full": row.get("contexts", []),
                "answer": row.get("answer", ""),
                "degraded": row.get("degraded", []),
                "scores": {
                    name: (None if _is_missing(s.get(name)) else s.get(name))
                    for name in names
                },
            }
        )

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": os.path.abspath(dataset_path),
        "metrics": names,
        "sample_count": len(records),
        "judge_retry_rounds": (diag or {}).get("retry_rounds", 0),
        "row_errors": (diag or {}).get("row_errors", []),
        "records": records,
    }
    out_dir = os.path.dirname(os.path.abspath(path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(
        "[Eval] 全量明细已落盘: %s（%d 条，失败行 %d）",
        os.path.abspath(path),
        len(records),
        len(payload["row_errors"]),
    )


def parse_args(argv: list[str]) -> tuple[str, str | None, int, int]:
    """解析命令行：位置参数=数据集路径，--dump-json、--judge-retries、--judge-max-tokens。"""
    dataset = DATASET_PATH
    dump_path: str | None = None
    retries = 2
    try:
        max_tokens = int(os.environ.get("EVAL_JUDGE_MAX_TOKENS", "8192"))
    except ValueError:
        max_tokens = 8192
    positional: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(
                "用法: python scripts/run_eval.py [数据集路径] "
                "[--dump-json 输出路径] [--judge-retries N] [--judge-max-tokens N]"
            )
            sys.exit(0)
        if arg in ("--dump-json", "--judge-retries", "--judge-max-tokens"):
            if i + 1 >= len(argv):
                logger.error("[Eval] %s 需要一个参数值", arg)
                sys.exit(1)
            value = argv[i + 1]
            i += 2
        elif arg.startswith("--dump-json=") or arg.startswith("--judge-retries=") or arg.startswith("--judge-max-tokens="):
            arg, value = arg.split("=", 1)
            i += 1
        else:
            positional.append(arg)
            i += 1
            continue
        if arg == "--dump-json":
            dump_path = value
        elif arg == "--judge-retries":
            try:
                retries = int(value)
            except ValueError:
                logger.error("[Eval] --judge-retries 需要整数，收到: %s", value)
                sys.exit(1)
        else:
            try:
                max_tokens = int(value)
            except ValueError:
                logger.error("[Eval] --judge-max-tokens 需要整数，收到: %s", value)
                sys.exit(1)
    if positional:
        dataset = positional[0]
    if retries < 0:
        logger.warning("[Eval] --judge-retries 为负数，按 0 处理（关闭重试）")
        retries = 0
    if max_tokens < 256:
        logger.warning("[Eval] --judge-max-tokens 过小（%d），按 8192 处理", max_tokens)
        max_tokens = 8192
    return dataset, dump_path, retries, max_tokens


async def main() -> None:
    global _JUDGE_CAPTURE
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # 0. 命令行参数（--help / 参数错误在任何环境下都应可用，故置于依赖检查之前）
    dataset_path, dump_path, judge_retries, judge_max_tokens = parse_args(sys.argv[1:])
    _JUDGE_CAPTURE = True  # judge 调用旁路记账：finish_reason / 长度 / 异常（诊断截断）

    # 0.1 ragas 就绪检查：依赖缺失时明确退出码 5，不裸抛 ImportError 栈
    if not RAGAS_AVAILABLE:
        logger.error(
            "[Eval] ragas 不可导入，评估终止（RAGAS_IMPORT_ERROR: %s）。"
            "请按锁重装依赖：uv sync --frozen --extra dev（锁内已含 pillow）",
            RAGAS_IMPORT_ERROR,
        )
        sys.exit(5)

    # 1. 数据集
    rows = load_dataset(dataset_path)

    # 2. 外部服务就绪检查（LLM 不可用直接退出码 2）
    await probe_llm()

    # 3. Qdrant + BM25 初始化（Qdrant 失败降级仅 BM25；来源全无则退出码 3）
    qdrant_ok, bm25_docs = await build_bm25_index()
    if not qdrant_ok and bm25_docs == 0:
        logger.error(
            "[Eval] 知识来源完全不可用：Qdrant 不可达且 %s 无文档，评估终止",
            KNOWLEDGE_DIR,
        )
        sys.exit(3)
    if not qdrant_ok:
        logger.warning("[Eval] 本次评估为降级模式（仅 BM25 召回，无向量召回）")

    # 4. 逐条跑 RAG 链路（检索 + 生成）
    #    每条样本内部会连打若干次 LLM（改写变体 + CRAG 评估 + 生成），
    #    对低 RPM 网关需要条间停一下，避免突发被 429（EVAL_GEN_PAUSE_S，默认 3 秒）
    gen_pause = max(0, int(os.environ.get("EVAL_GEN_PAUSE_S", "3")))
    logger.info("[Eval] 开始逐条执行 RAG 链路（%d 条）", len(rows))
    eval_rows = []
    for i, item in enumerate(rows, 1):
        logger.info("[Eval] [%d/%d] 检索生成: %s", i, len(rows), item["question"][:30])
        try:
            r = await run_rag(item)
        except Exception as e:  # 单条失败不中断整轮评估，明确记录为失败行
            logger.error("[Eval] [%d/%d] RAG 链路执行失败: %s", i, len(rows), e)
            r = {
                "question": item["question"],
                "ground_truth": item["ground_truth"],
                "adversarial": item["adversarial"],
                "contexts": [],
                "hit_docs": [],
                "answer": "",
                "degraded": ["rag_failed"],
            }
        if r["degraded"]:
            logger.warning(
                "[Eval] [%d/%d] 触发降级: %s", i, len(rows), ",".join(r["degraded"])
            )
        eval_rows.append(r)
        if gen_pause and i < len(rows):
            await asyncio.sleep(gen_pause)

    # 5. ragas 评估（捕获多生成退化：若有人把 answer_relevancy 换回旧版指标，
    #    会在 Instructor LLM 下退化为单点采样，此处检测防回归）
    degradation = _MultiGenDegradationCapture()
    logging.getLogger().addHandler(degradation)
    try:
        result, diag = await run_evaluation(
            eval_rows, judge_retries=judge_retries, judge_max_tokens=judge_max_tokens
        )
    finally:
        logging.getLogger().removeHandler(degradation)

    # 6. 输出明细 + 平均分 + 判分失败行清单
    all_nan_rows = print_report(eval_rows, result.scores, diag)

    # 7. 落盘必须在退出码判断之前：判分全失败时（退出码 4）更需要留下证据
    #    （2026-09-13 商汤 429 事故中，dump 在 exit 之后被跳过，现场没保住）
    _write_judge_calls(dump_path)
    if dump_path:
        dump_json(dump_path, dataset_path, eval_rows, result.scores, diag)

    if all_nan_rows == len(eval_rows):
        logger.error(
            "[Eval] 评估未产生任何有效得分（评分调用全部失败，"
            "请检查 LLM API key / 网络 / 模型 JSON 输出能力）"
        )
        sys.exit(4)

    if degradation.count:
        logger.warning(
            "[Eval] 检测到 answer_relevancy 多问题采样退化 %d 次（旧版指标在"
            " Instructor LLM 下退化为单点采样）。当前脚本使用 collections 版"
            " 循环采样（strictness=3），出现该警告说明指标被换回旧版",
            degradation.count,
        )

    # 7. judge 调用记录落盘 + 全量明细落盘：报告里的回答被截断，人工抽检必须以这些文件为准
    _write_judge_calls(dump_path)
    if dump_path:
        dump_json(dump_path, dataset_path, eval_rows, result.scores, diag)

    print("\n评估完成。")
    print("注：对抗样本（跨知识域/无对应文档/易混淆）检索不到对应文档属预期，相关指标会偏低。")


if __name__ == "__main__":
    asyncio.run(main())
