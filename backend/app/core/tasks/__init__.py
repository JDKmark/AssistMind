"""RQ 异步任务队列（P0）：耗时操作入队执行，HTTP 接口秒回 job_id。

背景：知识库重建（Qdrant scroll + BM25 build）、坏例回流导出、评估运行
都是分钟级耗时操作，此前同步执行会阻塞 HTTP 请求。本模块把三者收编为
可入队的任务函数，由独立 worker 进程消费（scripts/run_worker.py）。

设计：
- 队列复用既有 Redis（settings.REDIS_URL），RQ 自建连接（RedisClient 封装
  无队列语义，不强行复用）；与缓存/限流数据以 key 前缀天然隔离。
- 任务函数为模块级（RQ 按模块路径序列化引用）；async 函数由 RQ 2.x 原生支持。
- 统一 enqueue_task 入口：Retry(max=2) + 可配 timeout；失败必 logger（不静默）。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys

from redis import Redis
from rq import Queue, Retry
from rq.exceptions import NoSuchJobError
from rq.job import Job

from app.config import get_settings
from app.core.cache.semantic_cache import invalidate as cache_invalidate
from app.core.feedback_service import list_feedback, mark_exported
from app.core.infra.qdrant import get_qdrant
from app.core.rag import parsers
from app.core.rag.bm25 import get_bm25
from app.core.rag.chunking import chunk_text
from app.core.rag.embedding import embed_sync

logger = logging.getLogger(__name__)
settings = get_settings()

# app/core/tasks → app/data/eval_feedback.json
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "eval_feedback.json")

# app/core/tasks/__init__.py → backend/
_BACKEND_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# backend/ → 仓库根 knowledge/（上传与 seed 灌库的源文件都在这里）
KB_ROOT = os.path.join(os.path.dirname(_BACKEND_ROOT), "knowledge")

# 重灌可定位的源文件扩展名（与上传允许列表一致，外加 seed 库涉及的 .sql/.yml/.yaml）
_ALLOWED_EXTS = (".md", ".txt", ".pdf", ".docx", ".sql", ".yml", ".yaml")

BAD_SCORE_MAX = 2  # 差评阈值：score<=2 视为 bad case（与 export_feedback_badcases.py 一致）

_queue: Queue | None = None


def get_queue() -> Queue:
    """获取 RQ 队列（进程内缓存；worker 与 web 各自持有）。"""
    global _queue
    if _queue is None:
        connection = Redis.from_url(settings.REDIS_URL)
        _queue = Queue("assistmind", connection=connection)
    return _queue


def reset_queue() -> None:
    """重置队列缓存（测试隔离用）。"""
    global _queue
    _queue = None


def enqueue_task(func, *args, timeout: int = 1800, owner: str = "", **kwargs) -> Job:
    """入队并返回 RQ Job（统一 Retry(max=2) + timeout 配置）。

    注意：RQ 2.x enqueue 的超时参数名为 job_timeout（timeout 会透传为函数入参）。
    owner 为入队者身份（写入 job.meta），jobs API 据此校验查询归属（防横向越权）。
    """
    return get_queue().enqueue(
        func,
        *args,
        retry=Retry(max=2),
        job_timeout=timeout,
        meta={"owner": owner},
        **kwargs,
    )


def fetch_job(job_id: str) -> Job | None:
    """按 id 查询任务；不存在返回 None（不抛异常）。"""
    try:
        return Job.fetch(job_id, connection=get_queue().connection)
    except NoSuchJobError:
        return None


# ---------- 任务函数（RQ 按模块路径引用，勿改名） ----------


async def rebuild_knowledge_base() -> dict:
    """从 Qdrant 全量数据重建 BM25 索引。

    worker 进程不经过 FastAPI lifespan（没有 lifespan 里的 qdrant.connect()），
    任务内按需连接；connect() 幂等且失败自降级（is_connected 保持 False）。
    Qdrant 不可用时返回 error dict（worker 内不抛栈、不静默），由 jobs API 透出。
    """
    qdrant = get_qdrant()
    if not qdrant.is_connected:
        await qdrant.connect()  # worker 无 lifespan，按需建立连接
    if not qdrant.is_connected:
        logger.warning("[Task] rebuild_knowledge_base：Qdrant 不可用")
        return {"rebuilt": False, "error": "Qdrant 不可用，知识库重建暂不可用"}
    chunks = await qdrant.scroll_all()
    get_bm25().build(chunks)
    logger.info("[Task] rebuild_knowledge_base 完成：%s chunks", len(chunks))
    return {"rebuilt": True, "chunks": len(chunks)}


async def _ingest_from_file(
    file_path: str,
    filename: str,
    doc_id: str,
    title: str | None = None,
    category: str = "",
    security_group: list[str] | None = None,
    source: str | None = None,
    enabled: bool | None = None,
) -> dict:
    """单文档入库共用核心（上传 ingest 与 reingest 复用）。

    流程：读文件按扩展名提取文本 → 构造 metadata（chunk_text 透传进每个
    chunk payload）→ chunk_text → **先 embed_sync 全部 chunks 成功** →
    connect（幂等）→ delete_by_doc → upsert → BM25 全量重建 + mark_changed
    + 语义缓存失效。embedding 失败发生在删除之前，旧版本完整保留。

    Args:
        title: 文档标题（缺省用 filename；reingest 复用存量 chunk 的 title）
        category: 分类（缺省 "upload"）
        security_group: RBAC 角色列表（缺省全角色）
        source: 来源路径（缺省 knowledge/uploads/{filename}）
        enabled: 非 None 时写入 metadata，chunk payload 携带启停状态
    """
    try:
        if not os.path.exists(file_path):
            logger.warning("[Task] ingest：文件不存在 %s", file_path)
            return {"ingested": False, "error": f"文件不存在: {file_path}"}

        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext in (".pdf", ".docx"):
                text = parsers.extract_text(file_path)
            else:
                # .md/.txt/.sql/.yml/.yaml 纯文本直读
                with open(file_path, encoding="utf-8") as f:
                    text = f.read()
        except Exception as e:
            logger.warning("[Task] ingest：解析失败 %s: %s", filename, e)
            return {"ingested": False, "error": f"文档解析失败或内容为空: {filename}"}
        if not text or not text.strip():
            logger.warning("[Task] ingest：内容为空 %s", filename)
            return {"ingested": False, "error": f"文档解析失败或内容为空: {filename}"}

        # PDF/DOCX 无 Markdown 结构：文件名注入首位标题（与 seed 脚本一致，
        # 结构感知切块把标题并入首个 chunk，保留文档归属语义）
        if ext in (".pdf", ".docx"):
            text = f"# {filename}\n\n{text}"

        metadata = {
            "doc_id": doc_id,
            "title": title or filename,
            "source": source or f"knowledge/uploads/{filename}",
            "category": category or "upload",
            "security_group": security_group or ["user", "agent", "admin"],
        }
        if enabled is not None:
            metadata["enabled"] = enabled

        # chunk_text 把 metadata 展开进每个 chunk dict（**metadata），
        # enabled 无需 upsert 前另行注入
        chunks = chunk_text(text, metadata=metadata)
        if not chunks:
            logger.warning("[Task] ingest：切块为空 %s", filename)
            return {"ingested": False, "error": f"文档解析失败或内容为空: {filename}"}

        # 先 embedding 后删旧点：embedding 失败时旧版本完整保留（重灌安全契约）
        embeddings = embed_sync([c["text"] for c in chunks])
        if embeddings is None:
            logger.warning("[Task] ingest：embedding 失败，已保留旧版本 %s", doc_id)
            return {"ingested": False, "error": "embedding 失败，文档未更新（旧版本已保留）"}

        qdrant = get_qdrant()
        if not qdrant.is_connected:
            await qdrant.connect()  # worker 无 lifespan，按需建立连接
        if not qdrant.is_connected:
            logger.warning("[Task] ingest：Qdrant 不可用，%s 入库失败", filename)
            return {"ingested": False, "error": "Qdrant 不可用，文档入库失败"}

        await qdrant.delete_by_doc(doc_id)
        ok = await qdrant.upsert(chunks, embeddings)
        if not ok:
            logger.warning("[Task] ingest：upsert 失败 %s", doc_id)
            return {"ingested": False, "error": "Qdrant 写入失败，文档未更新"}

        # BM25 全量重建（seed_docs 的 build 只含本批 docs，不可依赖）；
        # 版本广播与语义缓存失效为旁路步骤，失败仅 warning 不回滚写入
        chunks_all = await qdrant.scroll_all()
        get_bm25().build(chunks_all)
        try:
            get_bm25().mark_changed()
        except Exception as e:
            logger.warning("[Task] ingest：BM25 版本广播失败（不影响入库）: %s", e)
        try:
            await cache_invalidate()
        except Exception as e:
            logger.warning("[Task] ingest：语义缓存失效失败（不影响入库）: %s", e)

        logger.info("[Task] ingest 完成：%s → %s（%s chunks）", filename, doc_id, len(chunks))
        return {"ingested": True, "doc_id": doc_id, "chunks": len(chunks)}
    except Exception as e:
        logger.exception("[Task] ingest 异常：%s", e)
        return {"ingested": False, "error": f"文档入库异常: {e}"}


async def ingest_uploaded_document(file_path: str, filename: str, category: str = "") -> dict:
    """上传文档入库任务（POST /knowledge/upload 校验通过后入队）。

    薄壳：校验文件存在 → 定 doc_id=uploads/<stem> → 共用核心 _ingest_from_file
    （解析/切块/embedding/先删后写/BM25 重建，详见其 docstring）。
    任一步失败返回含 error 的 dict（worker 内不抛栈、不静默，失败必 logger）。
    """
    if not os.path.exists(file_path):
        logger.warning("[Task] ingest_uploaded_document：文件不存在 %s", file_path)
        return {"ingested": False, "error": f"文件不存在: {file_path}"}
    doc_id = f"uploads/{os.path.splitext(os.path.basename(filename))[0]}"
    return await _ingest_from_file(file_path, filename, doc_id, category=category)


def resolve_source_path(doc_id: str) -> str | None:
    """按 doc_id 定位源文件（重灌用）。

    - uploads/ 前缀 → KB_ROOT/uploads/<stem>.{允许扩展名}
    - 其余 → KB_ROOT/mall 与 KB_ROOT/ops 递归匹配：文件名 stem 相等，
      或相对路径去扩展名相等（mall 的 doc_id 含子路径，如 business/xxx）
    - 多扩展名命中取 mtime 最新；无命中返回 None（不抛栈）
    """
    doc_id = (doc_id or "").strip().strip("/")
    if not doc_id:
        return None
    candidates: list[str] = []
    if doc_id.startswith("uploads/"):
        stem = doc_id[len("uploads/"):]
        updir = os.path.join(KB_ROOT, "uploads")
        if os.path.isdir(updir):
            for name in os.listdir(updir):
                base, ext = os.path.splitext(name)
                if ext.lower() in _ALLOWED_EXTS and base == stem:
                    candidates.append(os.path.join(updir, name))
    else:
        for sub in ("mall", "ops"):
            root = os.path.join(KB_ROOT, sub)
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    base, ext = os.path.splitext(name)
                    if ext.lower() not in _ALLOWED_EXTS:
                        continue
                    if base != doc_id:
                        rel = os.path.relpath(os.path.join(dirpath, name), root)
                        rel_stem = os.path.splitext(rel)[0].replace("\\", "/")
                        if rel_stem != doc_id:
                            continue
                    candidates.append(os.path.join(dirpath, name))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


async def reingest_document(doc_id: str) -> dict:
    """单文档增量重灌任务（POST /knowledge/{doc_id}/reingest 入队）。

    解析 doc_id 定位源文件 → 从 Qdrant 存量首个 chunk 复用元数据
    （title/source/category/security_group/enabled；无存量时用默认值，
    enabled 缺省视为启用）→ 共用核心 _ingest_from_file（先 embed 后删写，
    embedding 失败保留旧版本）。源文件不存在/失败返回含 error 的 dict
    （worker 内不抛栈、不静默，失败必 logger）。
    """
    try:
        path = resolve_source_path(doc_id)
        if path is None:
            logger.warning("[Task] reingest_document：未找到源文件 %s", doc_id)
            return {"ingested": False, "error": f"未找到源文件: {doc_id}"}

        qdrant = get_qdrant()
        if not qdrant.is_connected:
            await qdrant.connect()  # worker 无 lifespan，按需建立连接
        if not qdrant.is_connected:
            logger.warning("[Task] reingest_document：Qdrant 不可用，%s 重灌失败", doc_id)
            return {"ingested": False, "error": "Qdrant 不可用，文档重灌失败"}

        existing = await qdrant.scroll_by_doc(doc_id)
        first = existing[0] if existing else {}
        filename = os.path.basename(path)
        stem = os.path.splitext(filename)[0]
        logger.info(
            "[Task] reingest_document：%s → %s（存量 chunks=%d，复用元数据）",
            doc_id, path, len(existing),
        )
        return await _ingest_from_file(
            path,
            filename,
            doc_id,
            title=first.get("title") or stem,
            category=first.get("category") or "",
            security_group=first.get("security_group") or None,
            source=first.get("source") or None,
            enabled=first.get("enabled", True),
        )
    except Exception as e:
        logger.exception("[Task] reingest_document 异常：%s", e)
        return {"ingested": False, "error": f"文档重灌异常: {e}"}


def _load_existing_questions(out_path: str) -> set[str]:
    """读取已有评估集的 question 集合（去重用）。"""
    if not os.path.exists(out_path):
        return set()
    try:
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)
        return {str(x.get("question", "")).strip() for x in data if isinstance(x, dict)}
    except (OSError, ValueError):
        logger.warning("[Task] 读取现有 %s 失败，按空集处理", out_path)
        return set()


def _save_entries(out_path: str, entries: list[dict]) -> None:
    """合并写入评估集（保持已有条目）。

    原子写：先写临时文件再 os.replace，进程中断不会留下截断的 JSON
    （直接覆盖写一旦中断，下次读取失败会静默清空全部历史 bad case）。
    """
    existing: list = []
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, ValueError):
            logger.warning("[Task] 现有文件不可读，将覆盖重建: %s", out_path)
            existing = []
    merged = existing + entries
    tmp_path = f"{out_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, out_path)
    logger.info("[Task] 已写入 %s（共 %d 条）", out_path, len(merged))


async def export_badcases(limit: int = 50, out_path: str | None = None) -> dict:
    """把低分反馈（score<=2）回流为评估集 bad case（幂等，按 question 去重）。

    与 scripts/export_feedback_badcases.py 同逻辑（脚本保留壳调用本函数）。
    """
    path = out_path or OUT_PATH
    existing_qs = _load_existing_questions(path)

    page = 1
    page_size = min(limit * 5 or 100, 200)
    collected: list[dict] = []
    while page_size and len(collected) < limit:
        res = await list_feedback(score=None, exported=False, page=page, page_size=page_size)
        items = [
            it
            for it in res.get("items", [])
            if it.get("score", 5) <= BAD_SCORE_MAX
            and (it.get("query") or "").strip()
        ]
        for it in items:
            q = it["query"].strip()
            if q in existing_qs:
                continue  # 已回流过，跳过
            collected.append(it)
            existing_qs.add(q)
            if len(collected) >= limit:
                break
        total = res.get("total", 0)
        if page * page_size >= total:
            break
        page += 1

    if not collected:
        logger.info("[Task] 没有新的低分反馈待回流（score<=%d 且 query 非空）", BAD_SCORE_MAX)
        return {"exported": 0, "marked": 0}

    entries = [
        {
            "question": it["query"].strip(),
            "ground_truth": "",  # 无标准答案；run_eval 自动跳过 context_recall
            "adversarial": True,  # 差评样本视为对抗回归样本
            "source": f"feedback:{it.get('id', '')}",
            "trace_id": it.get("trace_id") or "",
        }
        for it in collected
    ]
    _save_entries(path, entries)
    marked = await mark_exported([it["id"] for it in collected])
    logger.info("[Task] 坏例回流完成：导出 %d 条，标记 %d 条", len(entries), marked)
    return {"exported": len(entries), "marked": marked}


def run_evaluation(dataset: str = "app/data/eval_feedback.json", timeout: int = 1800) -> dict:
    """以子进程运行评估脚本（依赖重：LLM + embedding，进程隔离最稳）。

    成功/失败都返回结构化结果（含 stdout/stderr 摘要），不向上抛异常。
    """
    cmd = [sys.executable, "scripts/run_eval.py", dataset]
    try:
        proc = subprocess.run(
            cmd,
            cwd=_BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.error("[Task] 评估超时（>%ss）：%s", timeout, dataset)
        return {"ok": False, "error": f"评估超时（>{timeout}s）", "stdout": "", "stderr": ""}

    ok = proc.returncode == 0
    if not ok:
        logger.error(
            "[Task] 评估失败（rc=%s）：%s stderr=%s",
            proc.returncode,
            dataset,
            (proc.stderr or "")[-500:],
        )
    else:
        logger.info("[Task] 评估完成：%s", dataset)
    return {
        "ok": ok,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-2000:],
    }
