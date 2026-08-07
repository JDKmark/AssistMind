"""RAGAS 评估脚本：对 RAG 链路跑 faithfulness / answer_relevancy / context_precision / context_recall。

用法（在 backend/ 下）：
    venv\\Scripts\\python.exe scripts/run_eval.py [数据集路径]

- 数据集路径缺省为 backend/app/data/eval_qa.json，也可传入自定义 JSON
  （字段：question / ground_truth / adversarial），便于跑部分样本做冒烟验证

流程：
1. 读取 backend/app/data/eval_qa.json（字段：question / ground_truth / adversarial），
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
  3  知识来源完全不可用（Qdrant 不可达且本地 knowledge/ops 无文档）
  4  评估未产生任何有效得分（评分调用全部失败）

注意：ragas 0.4.3 硬依赖 langchain_community.chat_models.vertexai，
而 langchain-community 0.4.x 已移除该模块，import ragas 前必须安装兼容垫片（见 _install_vertexai_shim）。
"""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import math
import os
import sys
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
from ragas import aevaluate  # noqa: E402
from ragas.dataset_schema import EvaluationDataset, SingleTurnSample  # noqa: E402
from ragas.embeddings.base import BaseRagasEmbedding, BaseRagasEmbeddings  # noqa: E402
from ragas.llms import llm_factory  # noqa: E402
from ragas.metrics import (  # noqa: E402
    context_precision,
    context_recall,
    faithfulness,
)
from ragas.metrics.collections import AnswerRelevancy as CollectionsAnswerRelevancy  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.infra.llm_factory import LLMUnavailableError, call_llm  # noqa: E402
from app.core.infra.qdrant import get_qdrant  # noqa: E402
from app.core.rag import engine  # noqa: E402
from app.core.rag.bm25 import get_bm25  # noqa: E402
from app.core.rag.chunking import chunk_text  # noqa: E402
from app.core.rag.embedding import embed_sync  # noqa: E402

logger = logging.getLogger(__name__)
settings = get_settings()

# backend/scripts/run_eval.py → backend/app/data/eval_qa.json
DATASET_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "data", "eval_qa.json")
# backend/scripts/run_eval.py → 仓库根 knowledge/ops（Qdrant 不可用时的 BM25 兜底来源）
KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "knowledge", "ops")

# RAGAS 指标（aevaluate 批处理部分）。answer_relevancy 单独走 collections 新版
# （循环采样 3 个反向问题取均值，不依赖 LLM 的 n 参数；旧版在 Instructor LLM 下
# 会退化为单点采样，见 run_evaluation 注释）
EVAL_METRICS = [faithfulness, context_precision, context_recall]


def load_dataset(path: str) -> list[dict]:
    """加载评估数据集，校验每条含非空 question / ground_truth。

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
        ground_truth = (item.get("ground_truth") or "").strip()
        if not question or not ground_truth:
            logger.warning(
                "[Eval] 跳过非法条目 #%d：question / ground_truth 不能为空", i
            )
            continue
        valid.append(
            {
                "question": question,
                "ground_truth": ground_truth,
                "adversarial": bool(item.get("adversarial", False)),
            }
        )

    logger.info(
        "[Eval] 数据集加载完成：%d 条有效（含 %d 条对抗样本），跳过 %d 条",
        len(valid),
        sum(1 for v in valid if v["adversarial"]),
        len(data) - len(valid),
    )
    if not valid:
        logger.error("[Eval] 数据集没有任何有效条目，无法评估")
        sys.exit(1)
    return valid


def _load_knowledge_docs() -> list[dict]:
    """读取 knowledge/ops/*.md 并分块（与 scripts/seed_ops_kb.py 一致）。"""
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
                "source": f"knowledge/ops/{doc_id}.md",
                "category": "ops",
                "security_group": ["user", "agent", "admin"],
            },
        )
        docs.extend(chunks)
    return docs


async def build_bm25_index() -> tuple[bool, int]:
    """构建 BM25 内存索引（与 app/main.py 启动逻辑一致）。

    - Qdrant 可用：从全量向量库 scroll_all 构建（与线上一致）
    - Qdrant 不可用：降级从 knowledge/ops 源文档构建（BM25 一等公民兜底）

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
    logger.warning("[Eval] Qdrant 不可用，降级为仅 BM25（从 knowledge/ops 源文档构建索引）")
    docs = _load_knowledge_docs()
    get_bm25().build(docs)
    logger.info("[Eval] BM25 索引构建 %d 文档（源文档降级模式）", len(docs))
    return False, len(docs)


async def probe_llm() -> None:
    """探测 LLM 可用性（DeepSeek → Ollama 降级链），不可用则报错退出（退出码 2）。"""
    if not settings.DEEPSEEK_API_KEY and settings.LLM_PROVIDER != "ollama":
        logger.error(
            "[Eval] 未配置 DEEPSEEK_API_KEY（LLM_PROVIDER=%s），"
            "无法生成回答与 ragas 评分，评估终止",
            settings.LLM_PROVIDER,
        )
        sys.exit(2)
    try:
        reply = await call_llm("请只回复两个字：正常", system="你是连通性测试助手。")
        logger.info("[Eval] LLM 连通性探测通过（回复: %s）", reply.strip()[:20])
    except LLMUnavailableError:
        logger.error("[Eval] LLM 不可用（DeepSeek 与 Ollama 均失败），评估无法继续")
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

    return {
        "question": question,
        "ground_truth": item["ground_truth"],
        "adversarial": item["adversarial"],
        "contexts": [c.get("text", "") for c in contexts],
        "hit_docs": hit_docs,
        "answer": gen["answer"],
        "degraded": retrieval["degraded"] + (["llm"] if gen["degraded"] else []),
    }


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


def build_ragas_llm(async_client: bool = False):
    """构造 ragas 评分 LLM：与项目配置一致（LLM_PROVIDER=deepseek → DeepSeek，
    LLM_PROVIDER=ollama → Ollama），OpenAI 兼容协议 + instructor 结构化输出。

    - async_client=False（默认）：同步 OpenAI client，供 aevaluate 批处理使用
      （同步路径并发调用更稳，Ollama 排队时不易触发 90s 超时）
    - async_client=True：AsyncOpenAI，供 collections 版指标使用（ascore 内部
      调用 llm.agenerate，同步 client 会报
      "Cannot use agenerate() with a synchronous client"）

    注意：ragas 指标调用是长 JSON 结构化输出，本地 Ollama 推理较慢，
    客户端超时放宽到 90s（离线批量评估，不走交互链路的 15s 降级超时）。
    """
    client_cls = openai.AsyncOpenAI if async_client else openai.OpenAI
    if settings.LLM_PROVIDER == "ollama":
        client = client_cls(
            api_key="ollama",
            base_url=settings.OLLAMA_BASE_URL + "/v1",
            timeout=90,
        )
        model = settings.OLLAMA_MODEL
    else:
        client = client_cls(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            timeout=settings.LLM_TIMEOUT,
        )
        model = settings.DEEPSEEK_MODEL
    return llm_factory(
        model,
        client=client,
        # 2048 在 deepseek-v4-flash 等长 JSON 结构化输出（ragas NLI 语句列表）下
        # 频繁截断 → IncompleteOutputException 重试拖慢且产生 NaN，调大到 4096
        max_tokens=4096,
    )


async def run_evaluation(rows: list[dict]):
    """构造 ragas 数据集并执行评估。

    - faithfulness / context_precision / context_recall：aevaluate 批处理
    - answer_relevancy：单独用 collections 新版 AnswerRelevancy 逐条 ascore——
      它在内部循环 strictness 次独立生成反向问题（不依赖 LLM 的 n 参数）。
      旧版（ragas.metrics.answer_relevancy）在 Instructor LLM 下只调用一次、
      退化为单点采样（Ollama 等不支持 n>1 的端点），分数是单次余弦相似度，
      方差大；collections 版为 3 问题均值，评估口径更可靠。

    失败行（raise_exceptions=False）由 ragas 记为 NaN，逐条明细中显示为 "-"，
    平均分按有效行计算，不静默掩盖。
    """
    samples = [
        SingleTurnSample(
            user_input=r["question"],
            retrieved_contexts=r["contexts"],
            response=r["answer"],
            reference=r["ground_truth"],
        )
        for r in rows
    ]
    dataset = EvaluationDataset(samples=samples)

    ragas_llm = build_ragas_llm()
    embeddings = ProjectEmbeddings()

    logger.info(
        "[Eval] 开始 ragas 评估：%d 条样本 × %d 项指标 + answer_relevancy(3 问题均值)（评分 LLM: %s / %s）",
        len(samples),
        len(EVAL_METRICS),
        settings.LLM_PROVIDER,
        settings.DEEPSEEK_MODEL if settings.LLM_PROVIDER != "ollama" else settings.OLLAMA_MODEL,
    )
    result = await aevaluate(
        dataset=dataset,
        metrics=EVAL_METRICS,
        llm=ragas_llm,
        embeddings=embeddings,
        raise_exceptions=False,
        show_progress=True,
    )

    # answer_relevancy：collections 版逐条循环采样（strictness=3）；
    # 需要 AsyncOpenAI client（ascore 内部调 llm.agenerate）
    ar_metric = CollectionsAnswerRelevancy(
        llm=build_ragas_llm(async_client=True), embeddings=embeddings, strictness=3
    )
    for row, score_row in zip(rows, result.scores):
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

    return result


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


def print_report(rows: list[dict], scores: list[dict]) -> int:
    """输出逐条明细 + 平均分。返回全部 NaN 的行数（全失败时调用方退出非 0）。"""
    # 打印项 = aevaluate 批处理指标 + collections 版 answer_relevancy（逐条 ascore 写入）
    names = [m.name for m in EVAL_METRICS] + ["answer_relevancy"]

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

    return all_nan_rows


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # 1. 数据集（支持自定义路径，便于部分样本冒烟验证）
    dataset_path = sys.argv[1] if len(sys.argv) > 1 else DATASET_PATH
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

    # 5. ragas 评估（捕获多生成退化：若有人把 answer_relevancy 换回旧版指标，
    #    会在 Instructor LLM 下退化为单点采样，此处检测防回归）
    degradation = _MultiGenDegradationCapture()
    logging.getLogger().addHandler(degradation)
    try:
        result = await run_evaluation(eval_rows)
    finally:
        logging.getLogger().removeHandler(degradation)

    # 6. 输出明细 + 平均分
    all_nan_rows = print_report(eval_rows, result.scores)
    if all_nan_rows == len(eval_rows):
        logger.error(
            "[Eval] 评估未产生任何有效得分（评分调用全部失败，"
            "请检查 DEEPSEEK_API_KEY / 网络 / 模型 JSON 输出能力）"
        )
        sys.exit(4)

    if degradation.count:
        logger.warning(
            "[Eval] 检测到 answer_relevancy 多问题采样退化 %d 次（旧版指标在"
            " Instructor LLM 下退化为单点采样）。当前脚本使用 collections 版"
            " 循环采样（strictness=3），出现该警告说明指标被换回旧版",
            degradation.count,
        )

    print("\n评估完成。")
    print("注：对抗样本（跨知识域/无对应文档/易混淆）检索不到对应文档属预期，相关指标会偏低。")


if __name__ == "__main__":
    asyncio.run(main())
