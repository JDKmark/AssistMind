"""BM25 关键词召回（rank_bm25，内存索引）。

BM25 是一等公民（论文 arXiv:2607.26497 启发）：
- 不可被关闭
- Qdrant 失败时 BM25 独立可用
- 查询成本几乎与规模无关

P1 修复：
- search 异步化（asyncio.to_thread 避免阻塞事件循环）
- 接入断路器（"bm25" breaker，失败计数后 Open）
- build/add/remove 仍同步（仅在初始化/索引更新时调用，不在请求路径）
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

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

    def build(self, docs: list[dict[str, Any]]) -> None:
        """构建索引（同步，仅在初始化时调用）。

        检索文本 = text + title + section_title + table_comment 拼接：
        SQL DDL 的 CREATE TABLE oms_order(...) 词面与自然语言查询差距大
        （"订单表 oms_order 是做什么的"），把表级注释（订单表）与标题注入
        打分词项，中文语义词才能命中（不改变 chunk 存储文本，仅索引侧增强）。
        """
        if not docs:
            self._bm25 = None
            self._docs = []
            return
        self._docs = docs
        tokenized = [
            _tokenize(
                f"{d.get('text', '')} {d.get('title', '')} "
                f"{d.get('section_title', '')} {d.get('table_comment', '')}"
            )
            for d in docs
        ]
        self._bm25 = BM25Okapi(tokenized)
        logger.info("[BM25] 索引构建完成，%d 文档", len(docs))

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

    async def search(
        self, query: str, top_k: int = 40, role: str = "user"
    ) -> list[dict[str, Any]]:
        """BM25 异步检索，含 RBAC 过滤。

        通过 asyncio.to_thread 在线程池中执行同步 BM25 计算，
        避免阻塞事件循环（rank_bm25 是纯 CPU 同步库）。

        BM25 是本地索引，无外部依赖，不接入断路器（不会因网络故障 Open）。
        但仍 try/except 防御性捕获异常，确保不阻断主链路。
        """
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
