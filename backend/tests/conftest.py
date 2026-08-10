"""pytest 全局 fixtures。

测试隔离原则（参考 AGENTS.md）：
- 每个 async 测试函数跑在独立 event loop
- aiosqlite 连接必须在同一 event loop 中创建和关闭
- 不在 async 测试中调用 asyncio.run()（嵌套 event loop）
- mock 外部依赖（Milvus/Redis/Neo4j/LLM/sentence-transformers），不连真实服务
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.infra import circuit_breaker


@pytest.fixture(autouse=True)
def reset_breakers() -> AsyncIterator[None]:
    """每个测试前后重置断路器状态（用内存存储，避免相互影响）。"""
    circuit_breaker.init_breakers(redis=None)
    yield
    circuit_breaker.init_breakers(redis=None)


@pytest.fixture(autouse=True)
def force_mock_ops_source(monkeypatch) -> None:
    """每个测试强制运维数据源为 mock 模式。

    避免 OPS_DATA_SOURCE=auto 时对真实 Prometheus 做健康探测（网络/超时/状态污染）。
    需要测试 real/auto 切换的用例自行 monkeypatch 覆盖 settings。
    """
    from app.config import Settings
    from app.core.ops import data_source

    monkeypatch.setattr(data_source, "settings", Settings(OPS_DATA_SOURCE="mock"))
    data_source.reset_source()
    yield
    data_source.reset_source()


@pytest.fixture(autouse=True)
def force_mock_mall_source(monkeypatch) -> None:
    """每个测试强制电商业务数据源为 mock 模式。

    避免 MALL_DATA_SOURCE=auto 时对真实 PostgreSQL 做健康探测（网络/超时/状态污染）。
    需要测试 real/auto 切换的用例自行 monkeypatch 覆盖 settings / _pg_healthy。
    """
    from app.config import Settings
    from app.core.mall import data_source

    monkeypatch.setattr(data_source, "settings", Settings(MALL_DATA_SOURCE="mock"))
    data_source.reset_source()
    yield
    data_source.reset_source()


@pytest.fixture(autouse=True)
def mock_ticket_queries() -> None:
    """mock 工单检索，避免诊断链路 collect 阶段连接真实 PostgreSQL。

    ops_supervisor.collect 现在会检索历史工单（search_tickets / list_tickets），
    PostgreSQL 不在单测范围内，统一 mock 掉（不 mock 时异步连接泄漏会产生
    Connection._cancel 警告）。
    """
    from unittest.mock import AsyncMock, patch

    with patch(
        "app.core.ops.pipeline.search_tickets", new=AsyncMock(return_value=[])
    ), patch(
        "app.core.ops.pipeline.list_tickets",
        new=AsyncMock(return_value={"tickets": [], "total": 0}),
    ):
        yield


@pytest.fixture
def mock_llm_success() -> Any:
    """mock call_llm 返回成功结果。"""
    with patch("app.core.infra.llm_factory._deepseek_with_retry", new=AsyncMock(return_value="LLM 响应")):
        with patch("app.core.infra.llm_factory._ollama_with_retry", new=AsyncMock(return_value="Ollama 响应")):
            yield


@pytest.fixture
def mock_llm_deepseek_fail_ollama_success() -> Any:
    """mock DeepSeek 失败，Ollama 成功。"""
    with patch(
        "app.core.infra.llm_factory._deepseek_with_retry",
        new=AsyncMock(side_effect=Exception("DeepSeek 不可用")),
    ):
        with patch("app.core.infra.llm_factory._ollama_with_retry", new=AsyncMock(return_value="Ollama 兜底响应")):
            yield


@pytest.fixture
def mock_llm_all_fail() -> Any:
    """mock 所有 LLM 失败。"""

    with patch(
        "app.core.infra.llm_factory._deepseek_with_retry",
        new=AsyncMock(side_effect=Exception("DeepSeek 不可用")),
    ), patch(
        "app.core.infra.llm_factory._ollama_with_retry",
        new=AsyncMock(side_effect=Exception("Ollama 不可用")),
    ):
        yield


@pytest.fixture
def mock_embedding() -> Any:
    """mock embed_async 返回假向量。

    注意：engine.py 中 `from app.core.rag.embedding import embed_async` 已绑定引用，
    必须同时 patch engine 模块中的引用才能生效。
    """
    async def _fake_embed(texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    with patch("app.core.rag.engine.embed_async", new=_fake_embed):
        with patch("app.core.rag.embedding.embed_async", new=_fake_embed):
            with patch("app.core.rag.embedding.embed_one", new=AsyncMock(return_value=[0.1, 0.2, 0.3])):
                yield


@pytest.fixture
def mock_embedding_fail() -> Any:
    """mock embed_async 失败返回 None。"""
    async def _fail_embed(texts: list[str]) -> list[list[float]] | None:
        return None

    with patch("app.core.rag.engine.embed_async", new=_fail_embed):
        with patch("app.core.rag.embedding.embed_async", new=_fail_embed):
            with patch("app.core.rag.embedding.embed_one", new=AsyncMock(return_value=None)):
                yield


@pytest.fixture
def mock_qdrant() -> Any:
    """mock Qdrant 返回固定检索结果。"""
    fake_results = [
        {"id": "1", "score": 0.9, "text": "AssistMind 是 SaaS 产品文档问答系统", "doc_id": "doc1", "title": "简介", "source": "intro.md"},
        {"id": "2", "score": 0.7, "text": "支持多路召回：向量 + BM25", "doc_id": "doc1", "title": "架构", "source": "arch.md"},
    ]
    qdrant_mock = MagicMock()
    qdrant_mock.is_connected = True
    qdrant_mock.search = AsyncMock(return_value=fake_results)
    qdrant_mock.upsert = AsyncMock(return_value=True)
    with patch("app.core.rag.engine.get_qdrant", return_value=qdrant_mock):
        yield qdrant_mock


@pytest.fixture
def mock_qdrant_fail() -> Any:
    """mock Qdrant 失败返回空。"""
    qdrant_mock = MagicMock()
    qdrant_mock.is_connected = False
    qdrant_mock.search = AsyncMock(return_value=[])
    with patch("app.core.rag.engine.get_qdrant", return_value=qdrant_mock):
        yield qdrant_mock


@pytest.fixture
def mock_reranker() -> Any:
    """mock rerank_async 返回排序后结果。"""
    async def _fake_rerank(query, docs, top_k=8):
        for i, d in enumerate(docs):
            d["rerank_score"] = 1.0 - i * 0.1
        return docs[:top_k]

    with patch("app.core.rag.engine.rerank_async", new=_fake_rerank):
        yield


@pytest.fixture
def mock_reranker_fail() -> Any:
    """mock rerank_async 失败返回 None。"""
    async def _fail_rerank(query, docs, top_k=8):
        return None

    with patch("app.core.rag.engine.rerank_async", new=_fail_rerank):
        yield


@pytest.fixture
def bm25_with_docs() -> Any:
    """构造带文档的 BM25 索引。"""
    from app.core.rag.bm25 import BM25Index

    bm25 = BM25Index()
    bm25.build([
        {"text": "AssistMind 是 SaaS 产品文档问答系统", "doc_id": "doc1", "title": "简介", "source": "intro.md", "security_group": ["user", "agent", "admin"]},
        {"text": "支持多路召回：向量 + BM25", "doc_id": "doc1", "title": "架构", "source": "arch.md", "security_group": ["user", "agent", "admin"]},
        {"text": "失败降级包括断路器和重试", "doc_id": "doc2", "title": "降级", "source": "fallback.md", "security_group": ["user", "agent", "admin"]},
    ])
    with patch("app.core.rag.engine.get_bm25", return_value=bm25):
        yield bm25
