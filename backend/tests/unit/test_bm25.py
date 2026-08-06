"""BM25Index 内存索引接口单元测试。

覆盖 build/search/top_k/RBAC 过滤/增量 add/remove/空索引/singleton 等
接口行为（phase6-retrieval-quality Task 1 要求接口不变）。
分词细节（jieba 词粒度/整词/停用词）见 test_bm25_tokenize.py。
"""

from __future__ import annotations

from app.core.rag.bm25 import BM25Index, get_bm25


def _doc(doc_id: str, text: str, sg=("user", "agent", "admin")) -> dict:
    return {
        "doc_id": doc_id,
        "text": text,
        "title": doc_id,
        "source": "t.md",
        "security_group": list(sg),
    }


async def test_search_returns_top_k_with_full_fields():
    """search 返回 top_k 条，字段完整，相关文档排名第一。"""
    index = BM25Index()
    index.build([
        _doc("d1", "连接池耗尽导致数据库连接失败"),
        _doc("d2", "前端页面使用 Vue3 和 Element Plus 构建"),
        _doc("d3", "工单系统支持 SLA 跟踪和自动分派"),
    ])
    results = await index.search("连接池耗尽", top_k=2)
    assert len(results) == 2
    assert results[0]["doc_id"] == "d1"
    assert results[0]["score"] > 0
    # 当前 search 结果 schema：基础字段 + section_title/table_comment（与 qdrant 召回对齐）
    assert set(results[0]) == {"doc_id", "title", "source", "text", "score", "section_title", "table_comment"}
    assert results[0]["section_title"] == ""
    assert results[0]["table_comment"] == ""


async def test_search_empty_index_returns_empty():
    """空索引 search 返回空列表。"""
    index = BM25Index()
    assert await index.search("连接池") == []


async def test_add_and_remove_rebuild_index():
    """增量 add / remove_by_doc 后索引生效。"""
    index = BM25Index()
    index.add(_doc("d1", "连接池耗尽"))
    assert index.doc_count == 1
    assert len(await index.search("连接池", top_k=5)) == 1

    index.add(_doc("d2", "前端页面"))
    assert index.doc_count == 2

    index.remove_by_doc("d1")
    assert index.doc_count == 1
    # 剩余文档不含查询词，检索结果应全部为 0 分（现有实现不截断 0 分文档）
    results = await index.search("连接池", top_k=5)
    assert {r["doc_id"] for r in results} == {"d2"}
    assert all(r["score"] == 0.0 for r in results)


async def test_search_respects_rbac_security_group():
    """RBAC 过滤：role 无权访问的文档不出现在结果中。"""
    index = BM25Index()
    index.build([
        _doc("user_doc", "连接池耗尽", sg=["user", "agent", "admin"]),
        _doc("admin_doc", "连接池耗尽", sg=["admin"]),
    ])
    results = await index.search("连接池", top_k=10, role="user")
    assert {r["doc_id"] for r in results} == {"user_doc"}

    results = await index.search("连接池", top_k=10, role="admin")
    assert {r["doc_id"] for r in results} == {"user_doc", "admin_doc"}


def test_get_bm25_singleton():
    """get_bm25 返回单例。"""
    assert get_bm25() is get_bm25()
