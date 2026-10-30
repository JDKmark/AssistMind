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


def _doc_with_meta(doc_id: str, text: str, title: str) -> dict:
    """带标题/表注释的文档（验证索引文本注入口径）。"""
    return {
        "doc_id": doc_id,
        "text": text,
        "title": title,
        "section_title": "",
        "table_comment": title,
        "source": "t.md",
        "security_group": ["user", "agent", "admin"],
    }


def _ddl_docs() -> list[dict]:
    """五个 SQL DDL chunk：语义词只出现在 title / table_comment 里。

    对应真实语料形态——chunk 正文是 CREATE TABLE oms_order(...)，
    中文语义（订单表）只在表级注释与标题上。
    """
    return [
        _doc_with_meta("d1", "CREATE TABLE oms_order (id bigint)", "订单表"),
        _doc_with_meta("d2", "CREATE TABLE ums_member (id bigint)", "会员表"),
        _doc_with_meta("d3", "CREATE TABLE pms_product (id bigint)", "商品表"),
        _doc_with_meta("d4", "CREATE TABLE oms_cart_item (id bigint)", "购物车表"),
        _doc_with_meta("d5", "CREATE TABLE sms_coupon (id bigint)", "优惠券表"),
    ]


def _scores(results: list[dict]) -> list[tuple[str, float]]:
    return [(r["doc_id"], round(r["score"], 9)) for r in results]


async def test_remove_by_doc_matches_full_rebuild():
    """增量移除后的索引与「用剩余文档全量重建」完全等价。

    回归背景：add / remove_by_doc 曾只对 text 做 tokenize，而 build 用
    text + title + section_title + table_comment 拼接。一次文档删除就会让
    整个索引的标题注入消失，中文语义查询（「订单表 oms_order 是做什么的」）
    退化为只匹配 DDL 词面；且本进程 mark_changed 推进版本后不会自愈。
    """
    index = BM25Index()
    index.build(_ddl_docs())
    index.remove_by_doc("d2")

    fresh = BM25Index()
    fresh.build([d for d in _ddl_docs() if d["doc_id"] != "d2"])

    # 「订单表」不在任何 text 中，只靠 title / table_comment 命中
    assert (await index.search("订单表", top_k=5))[0]["doc_id"] == "d1"
    assert _scores(await index.search("订单表", top_k=5)) == _scores(
        await fresh.search("订单表", top_k=5)
    )
    assert _scores(await index.search("优惠券表", top_k=5)) == _scores(
        await fresh.search("优惠券表", top_k=5)
    )


async def test_add_matches_full_rebuild():
    """增量 add 后的索引与「含新文档的全量重建」完全等价。"""
    docs = _ddl_docs()
    index = BM25Index()
    index.build(docs[:4])
    index.add(docs[4])

    fresh = BM25Index()
    fresh.build(docs)

    assert (await index.search("优惠券表", top_k=5))[0]["doc_id"] == "d5"
    assert _scores(await index.search("优惠券表", top_k=5)) == _scores(
        await fresh.search("优惠券表", top_k=5)
    )


async def test_empty_index_text_does_not_crash_build():
    """索引文本全为空时构建不抛异常（BM25Okapi 在 avgdl=0 时除零）。

    真实触发面：text/title/section_title/table_comment 全空的坏 chunk。
    原实现会 ZeroDivisionError，让整批灌库中断。
    """
    index = BM25Index()
    index.build([{"doc_id": "x", "text": "", "title": ""}])
    assert index.doc_count == 1
    assert await index.search("任何查询") == []

    index.add({"doc_id": "y", "text": "", "title": ""})
    assert index.doc_count == 2
    assert await index.search("任何查询") == []


async def test_search_tolerates_null_security_group_and_missing_text():
    """单个字段缺失/为 null 的文档不应让整次检索返回空。

    security_group 显式为 null、或文档缺 text 字段时，原实现分别抛
    TypeError / KeyError，被 search 的兜底 except 吞掉后整库检索返回 []。
    """
    index = BM25Index()
    index.build([
        {"doc_id": "null_sg", "text": "连接池耗尽", "security_group": None},
        {"doc_id": "no_text", "title": "连接池", "security_group": ["user"]},
    ])

    results = await index.search("连接池", top_k=5, role="user")
    assert {r["doc_id"] for r in results} == {"null_sg", "no_text"}
    assert all(r["score"] != 0.0 for r in results)
