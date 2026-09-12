"""BM25 关键词召回（rank_bm25，内存索引）。

BM25 是一等公民（论文 arXiv:2607.26497 启发）：
- 不可被关闭
- Qdrant 失败时 BM25 独立可用
- 查询成本几乎与规模无关

P1 修复：
- search 异步化（asyncio.to_thread 避免阻塞事件循环）
- 接入断路器（"bm25" breaker，失败计数后 Open）
- build/add/remove 仍同步（仅在初始化/索引更新时调用，不在请求路径）

kb-management-p0（跨进程版本同步）：
BM25 是进程内内存索引，web 与 worker 各持一份。沿用语义缓存
「Redis 版本号 O(1) 失效 + 惰性重建」同构模式（key: assistmind:kb:bm25:version）：
- 变更方（delete 端点 / ingest 任务 / rebuild 任务 / seeder 实际写入）完成后
  调 mark_changed() INCR 版本号广播
- 检索方 search 入口经 ensure_fresh() GET 版本，落后则后台惰性重载
  （Qdrant scroll_all → 原子替换索引），重建完成前旧索引继续服务
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import jieba
import redis as redis_lib
from rank_bm25 import BM25Okapi

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# BM25 跨进程版本号 key（与语义缓存 _VERSION_KEY 模式同构，独立 key 互不干扰）
BM25_VERSION_KEY = "assistmind:kb:bm25:version"

# Redis 版本检查失败 warning 节流：同一分钟只记一次
# （ensure_fresh 在每条检索路径上，Redis 故障时不刷屏）
_last_redis_warn_ts: float = 0.0
_REDIS_WARN_INTERVAL_SECONDS = 60.0

# 版本号 Redis 同步客户端单例（懒创建，redis.Redis 自带连接池、断线自动重连）
_version_redis: redis_lib.Redis | None = None


def _warn_redis_once_per_minute(message: str) -> None:
    global _last_redis_warn_ts
    now = time.monotonic()
    if now - _last_redis_warn_ts >= _REDIS_WARN_INTERVAL_SECONDS:
        _last_redis_warn_ts = now
        logger.warning(message)


def _get_version_redis() -> redis_lib.Redis:
    """BM25 版本号专用 Redis 连接（同步客户端，测试注入点）。

    同步客户端的原因：mark_changed 供同步上下文调用（RQ 任务函数运行在
    无事件循环的 worker 进程、API 端点内也有同步调用方），必须用同步
    客户端才能工作；async 侧（ensure_fresh）经 asyncio.to_thread 消费
    同一连接。测试 patch 本函数返回 fakeredis.FakeRedis()。
    """
    global _version_redis
    if _version_redis is None:
        _version_redis = redis_lib.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _version_redis


async def _load_docs_from_qdrant() -> list[dict[str, Any]]:
    """从 Qdrant 全量拉取 chunk（后台惰性重载的数据源，测试注入点）。

    延迟 import：bm25 被 RQ 任务/单测 import 时不应触发 Qdrant 依赖。
    worker 进程不经过 FastAPI lifespan（没有 connect），使用前按需连接
    （幂等）。连接失败 / 拉取失败统一返回 []，调用方据此不推进
    _seen_version 以便下次重试。
    """
    try:
        from app.core.infra.qdrant import get_qdrant

        qdrant = get_qdrant()
        if not qdrant.is_connected:
            await qdrant.connect()
        if not qdrant.is_connected:
            logger.warning("[BM25] 重载中止：Qdrant 不可用，保留旧索引")
            return []
        return await qdrant.scroll_all()
    except Exception as e:
        logger.warning("[BM25] 重载拉取文档失败: %s", e)
        return []

# jieba 首次加载字典有秒级延迟，模块顶部初始化（含预热），避免请求路径首次命中卡顿
jieba.initialize()
# 抑制 jieba 构建前缀字典/加载缓存的 INFO 日志噪音
jieba.setLogLevel(logging.WARNING)


# 高频虚词停用表：中文助词/介词/代词 + 常见英文虚词。
# 这些词在几乎每篇文档都出现，token 化了只会稀释 IDF 区分度。
_STOPWORDS: frozenset[str] = frozenset(
    {
        # 助词/语气词
        "的", "了", "着", "过", "地", "得", "吗", "呢", "吧", "啊", "呀", "哦", "嗯", "么",
        # 介词/连词
        "是", "在", "和", "与", "及", "或", "等", "之", "对", "从", "到", "向", "于",
        "而", "但", "并", "且", "因为", "所以", "如果", "然后", "以及", "关于",
        # 代词/指代
        "这", "那", "这个", "那个", "这些", "那些", "这样", "那样", "这是", "那是",
        "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们", "它们",
        # 高频量词/副词/虚化动词
        "个", "种", "些", "就", "都", "也", "很", "被", "把", "为", "以", "其", "又",
        "再", "只", "还", "要", "会", "能", "可以", "应该", "需要", "进行", "一下",
        "相关", "以下", "如下", "其中", "之一",
        # 常见英文虚词
        "a", "an", "the", "and", "or", "of", "in", "on", "to", "for", "with",
        "is", "are", "be", "was", "were", "at", "by", "it", "this", "that",
    }
)

# 英文单词/数字/下划线标识符整体保留（order_item、user_id、max_pool_size 不拆成泛词）
_IDENT_RE = re.compile(r"[a-zA-Z0-9_]+")
# 连续中文块（交给 jieba 做词粒度切分）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    """词粒度分词：中文 jieba.lcut，英文/数字/标识符整词保留，过滤停用词。

    相比旧的"单字 unigram"，词粒度让 IDF 具备区分度：
    - "连接池耗尽" -> ["连接池", "耗尽"]（词粒度，而非 连/接/池/耗/尽）
    - "order_item" -> ["order_item"]（整词保留，不被拆成 order/item）
    - 高频虚词（的/了/是/在...）被过滤，不参与打分

    函数签名不变：_tokenize(text) -> list[str]（小写 token 列表）。
    """
    tokens: list[str] = []
    for part in _IDENT_RE.findall(text):
        tokens.append(part.lower())
    for cjk_run in _CJK_RE.findall(text):
        tokens.extend(jieba.lcut(cjk_run))
    return [t for t in tokens if t not in _STOPWORDS]


class BM25Index:
    """BM25 内存索引。"""

    def __init__(self) -> None:
        self._bm25: BM25Okapi | None = None
        self._docs: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        # 跨进程版本同步状态（见模块 docstring）
        self._seen_version: int = 0
        self._reload_task: asyncio.Task | None = None

    @staticmethod
    def _build_bm25_from_docs(docs: list[dict[str, Any]]) -> BM25Okapi:
        """tokenized 构建（build 与后台重载 _reload 共用，避免重复）。"""
        tokenized = [
            _tokenize(
                f"{d.get('text', '')} {d.get('title', '')} "
                f"{d.get('section_title', '')} {d.get('table_comment', '')}"
            )
            for d in docs
        ]
        return BM25Okapi(tokenized)

    def _read_version_quiet(self) -> int:
        """读取当前 Redis 版本（key 不存在 / Redis 失败均视为 0），build 对齐用。"""
        try:
            val = _get_version_redis().get(BM25_VERSION_KEY)
            return int(val) if val is not None else 0
        except Exception as e:
            logger.warning("[BM25] 构建后读取版本号失败，视为 0: %s", e)
            return 0

    def build(self, docs: list[dict[str, Any]]) -> None:
        """构建索引（同步，仅在初始化时调用）。

        检索文本 = text + title + section_title + table_comment 拼接：
        SQL DDL 的 CREATE TABLE oms_order(...) 词面与自然语言查询差距大
        （"订单表 oms_order 是做什么的"），把表级注释（订单表）与标题注入
        打分词项，中文语义词才能命中（不改变 chunk 存储文本，仅索引侧增强）。

        成功构建后对齐当前 Redis 版本号：避免进程启动 / 本地重建后
        ensure_fresh 立即误判「版本落后」触发重载。
        """
        if not docs:
            self._bm25 = None
            self._docs = []
            self._seen_version = self._read_version_quiet()
            return
        self._docs = docs
        self._bm25 = self._build_bm25_from_docs(docs)
        self._seen_version = self._read_version_quiet()
        logger.info("[BM25] 索引构建完成，%d 文档（版本=%d）", len(docs), self._seen_version)

    def _search_sync(
        self, query: str, top_k: int, role: str
    ) -> list[dict[str, Any]]:
        """同步搜索实现（在线程池中执行）。"""
        if not self._bm25 or not self._docs:
            return []
        tokens = _tokenize(query)
        scores = self._bm25.get_scores(tokens)
        filtered: list[tuple[int, float]] = []
        for i, doc in enumerate(self._docs):
            sg = doc.get("security_group", ["user", "agent", "admin"])
            if role in sg:
                filtered.append((i, float(scores[i])))
        filtered.sort(key=lambda x: x[1], reverse=True)
        top = filtered[:top_k]
        return [
            {
                "doc_id": self._docs[i]["doc_id"],
                "title": self._docs[i].get("title", ""),
                "source": self._docs[i].get("source", ""),
                "section_title": self._docs[i].get("section_title", ""),
                "table_comment": self._docs[i].get("table_comment", ""),
                "text": self._docs[i]["text"],
                "score": s,
            }
            for i, s in top
        ]

    async def ensure_fresh(self) -> None:
        """版本检查：落后则后台触发一次惰性重载（不阻塞当前请求）。

        - Redis 失败：跳过检查（warning 节流），当前索引继续服务
        - 版本一致（含本进程 mark_changed/build 已对齐）：直接返回，不重载自己
        - 重载进行中：复用进行中的任务，不重复创建（防任务堆积）
        - 同一落后版本的重载任务已在跑时不会堆积；任务由 _reload finally 清空
        """
        try:
            cur = await asyncio.to_thread(self._read_version_for_check)
        except Exception as e:  # 防御：版本检查自身异常不阻断检索主链路
            _warn_redis_once_per_minute(f"[BM25] 版本检查失败，跳过: {e}")
            return
        if cur is None or cur == self._seen_version:
            return
        if self._reload_task is not None and not self._reload_task.done():
            return
        self._reload_task = asyncio.create_task(self._reload(cur))

    def _read_version_for_check(self) -> int | None:
        """ensure_fresh 的同步版本读取（在线程池中执行）。None = Redis 不可用。"""
        try:
            val = _get_version_redis().get(BM25_VERSION_KEY)
            return int(val) if val is not None else 0
        except Exception as e:
            _warn_redis_once_per_minute(f"[BM25] 版本检查 Redis 不可用，跳过重载检查: {e}")
            return None

    async def _reload(self, target_version: int) -> None:
        """后台惰性重载：scroll_all 全量拉取 → 原子替换索引 → 推进 _seen_version。

        - Qdrant 不可用（拉取返回 []）：不推进版本（下次检索重试），旧索引保留
        - 替换是整体引用替换（先构建新 BM25 再一次性换 _docs/_bm25），
          并发 search 读到的是替换前或替换后的完整索引
        - 全程 try/except，失败仅 warning，不影响旧索引继续服务
        """
        try:
            docs = await _load_docs_from_qdrant()
            if not docs:
                logger.warning(
                    "[BM25] 重载未获取到文档，保留旧索引（版本 %d 不推进，下次重试）",
                    target_version,
                )
                return
            new_bm25 = self._build_bm25_from_docs(docs)
            self._docs = docs
            self._bm25 = new_bm25
            self._seen_version = target_version
            logger.info("[BM25] 后台重载完成：%d 文档（版本=%d）", len(docs), target_version)
        except Exception as e:
            logger.warning("[BM25] 后台重载失败: %s", e)
        finally:
            self._reload_task = None

    def mark_changed(self) -> None:
        """本进程完成索引变更（delete/ingest/rebuild/seeder 实际写入）后调用。

        INCR Redis 版本号广播给其他进程，并把本地 _seen_version 同步为新值
        （本进程刚变更完，无需重载自己）。Redis 失败仅 warning，不阻塞变更方
        （其他进程将在 Redis 恢复后于下次检索时感知旧版本并重载）。
        """
        try:
            self._seen_version = int(_get_version_redis().incr(BM25_VERSION_KEY))
            logger.info("[BM25] 索引版本已广播: %d", self._seen_version)
        except Exception as e:
            logger.warning("[BM25] mark_changed 广播版本失败: %s", e)

    async def search(
        self, query: str, top_k: int = 40, role: str = "user"
    ) -> list[dict[str, Any]]:
        """BM25 异步检索，含 RBAC 过滤。

        通过 asyncio.to_thread 在线程池中执行同步 BM25 计算，
        避免阻塞事件循环（rank_bm25 是纯 CPU 同步库）。

        入口先做跨进程版本检查（ensure_fresh，亚毫秒 Redis GET），
        必须发生在 to_thread 检索之前：版本落后时旧索引先应答本次请求，
        新索引由后台任务就位后生效（惰性重载）。

        BM25 是本地索引，无外部依赖，不接入断路器（不会因网络故障 Open）。
        但仍 try/except 防御性捕获异常，确保不阻断主链路。
        """
        await self.ensure_fresh()
        try:
            return await asyncio.to_thread(self._search_sync, query, top_k, role)
        except Exception as e:
            logger.warning("[BM25] search 失败: %s", e)
            return []

    @property
    def doc_count(self) -> int:
        return len(self._docs)

    def add(self, doc: dict[str, Any]) -> None:
        """增量添加（重建索引，同步）。"""
        self._docs.append(doc)
        tokenized = [_tokenize(d["text"]) for d in self._docs]
        self._bm25 = BM25Okapi(tokenized)

    def remove_by_doc(self, doc_id: str) -> None:
        """按 doc_id 移除（重建索引，同步）。"""
        self._docs = [d for d in self._docs if d.get("doc_id") != doc_id]
        if self._docs:
            tokenized = [_tokenize(d["text"]) for d in self._docs]
            self._bm25 = BM25Okapi(tokenized)
        else:
            self._bm25 = None


_bm25: BM25Index | None = None


def get_bm25() -> BM25Index:
    global _bm25
    if _bm25 is None:
        _bm25 = BM25Index()
    return _bm25
