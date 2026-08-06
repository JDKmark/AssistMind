"""BM25 _tokenize 词粒度分词单元测试（phase6-retrieval-quality Task 1）。

验证升级后的分词行为：
- 中文 jieba 词粒度切分（不再是单字 unigram，IDF 重新具备区分度）
- 英文单词/数字/下划线标识符整词保留（order_item、user_id、max_pool_size 不拆成泛词）
- 高频虚词（的/了/是/在...）停用过滤
- 关键：词粒度下 BM25 得分显著区分相关/无关文档（旧 unigram 几乎无区分度）
"""

from __future__ import annotations

from app.core.rag.bm25 import BM25Index, _tokenize


def test_tokenize_chinese_word_level():
    """中文应按词粒度切分，而不是单字。"""
    tokens = _tokenize("连接池耗尽")
    assert tokens == ["连接池", "耗尽"]
    # 不是单字 unigram：不应出现单字 token
    assert "连" not in tokens
    assert "池" not in tokens
    assert "耗" not in tokens


def test_tokenize_identifier_kept_whole():
    """下划线标识符应整词保留，不被拆成泛词（order/item/id/...）。"""
    assert _tokenize("order_item") == ["order_item"]
    assert _tokenize("user_id") == ["user_id"]
    assert _tokenize("max_pool_size") == ["max_pool_size"]
    # 混合文本中标识符同样不被拆
    assert _tokenize("user_id 不存在") == ["user_id", "不", "存在"]
    # 归一为小写
    assert _tokenize("Order_Item") == ["order_item"]
    # 数字标识符整词保留
    assert _tokenize("error_code_500") == ["error_code_500"]


def test_tokenize_filters_stopwords():
    """高频虚词（的/了/是/在...）应被过滤。"""
    tokens = _tokenize("这是关于连接池的说明文档")
    assert "的" not in tokens
    assert tokens == ["连接池", "说明", "文档"]
    assert _tokenize("连接池在运行中会耗尽") == ["连接池", "运行", "中", "耗尽"]


def test_tokenize_mixed_chinese_and_identifier():
    """中英混合：中文词粒度 + 标识符整词，顺序保持。"""
    assert _tokenize("order_item 连接池耗尽") == ["order_item", "连接池", "耗尽"]


async def test_bm25_discrimination_relevant_doc_scores_much_higher():
    """关键：词粒度分词下 BM25 得分应显著区分相关与无关文档。

    背景：旧单字 unigram 下，任何中文查询与任何中文文档都共享大量单字
    （如"连/接/池/耗/尽"散落在各文档），IDF 区分度近乎失效；
    升级为词粒度后，"连接池""耗尽"只在相关文档出现，得分拉开数量级差距。

    实测数字（jieba 0.42.1 + rank_bm25 0.2.2，见下方 docs 构造）：
      related=0.575（含"连接池"+"耗尽"两个查询词）
      partial=0.116（仅含"连接池"一个查询词）
      unrelated=0.0（无任何查询词）
    故断言 related > 2 × max(partial, unrelated)，余量约 2.5 倍，
    且该断言在单字 unigram 下不成立（单字下 related/partial 分差远小于 2 倍）。
    """
    docs = [
        {
            "doc_id": "related",
            "text": "连接池耗尽会导致数据库连接失败，需要调大最大连接数，重启服务后恢复",
            "title": "连接池故障处理",
            "source": "ops.md",
            "security_group": ["user", "agent", "admin"],
        },
        {
            "doc_id": "partial",
            "text": "连接池监控报表每小时生成，展示当前活跃连接数",
            "title": "连接池监控",
            "source": "ops.md",
            "security_group": ["user", "agent", "admin"],
        },
        {
            "doc_id": "unrelated",
            "text": "前端页面使用 Vue3 和 Element Plus 构建，支持暗色主题",
            "title": "前端架构",
            "source": "front.md",
            "security_group": ["user", "agent", "admin"],
        },
    ]
    index = BM25Index()
    index.build(docs)
    results = await index.search("连接池耗尽", top_k=3)

    scores = {r["doc_id"]: r["score"] for r in results}
    assert results[0]["doc_id"] == "related", f"相关文档应排名第一: {results}"
    assert results[1]["doc_id"] == "partial", f"部分相关应排名第二: {results}"

    # 相关文档得分显著高于无关文档（2 倍以上，实测余量约 2.5 倍）
    assert scores["related"] > 2 * scores["partial"], scores
    assert scores["related"] > 2 * scores["unrelated"], scores
    # 部分相关（共享一个查询词）应高于完全无关，得分具有梯度
    assert scores["partial"] > scores["unrelated"], scores
