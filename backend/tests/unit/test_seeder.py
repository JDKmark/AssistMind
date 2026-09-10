"""知识库灌库公共逻辑（seeder）单元测试。

覆盖：
- Qdrant 不可用：返回统计 dict + qdrant_ok=False（不抛异常，灌库脚本可继续输出切分统计）
- 正常流程：chunk → embedding → upsert → BM25 重建，统计数字正确
- embedding 失败：跳过该文档（logger.warning，不中断其他文档）
- reset 模式：先按 doc_id 清空再写入
- Qdrant upsert 确定性 id（uuid5(doc_id:chunk_index)）：重复 seed 幂等覆盖不翻倍

mock 策略：mock seeder 中的 get_qdrant / embed_sync / get_bm25 引用，
chunk_text 走真实实现（纯逻辑，不依赖外部服务）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.core.rag.seeder import seed_docs

DOCS = [
    {
        "doc_id": "mall/business/pricing",
        "title": "价格优惠",
        "text": "# 价格优惠\n\n满减活动：满 300 减 30，会员额外 95 折。\n\n包邮门槛：满 99 包邮。",
    },
    {
        "doc_id": "mall/business/refund",
        "title": "售后政策",
        "text": "# 售后政策\n\n七天无理由退货，运费由买家承担。",
    },
]


def _metadata_fn(doc: dict) -> dict:
    return {
        "doc_id": doc["doc_id"],
        "title": doc["title"],
        "source": f"{doc['doc_id']}.md",
        "category": "mall",
    }


class FakeQdrant:
    """可编程假 Qdrant：记录 upsert/delete 调用，is_connected 可控。"""

    def __init__(self, connected: bool = True):
        self._connected = connected
        self.upsert_calls: list[tuple[list, list]] = []
        self.deleted: list[str] = []
        self.closed = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    async def delete_by_doc(self, doc_id: str) -> bool:
        self.deleted.append(doc_id)
        return True

    async def upsert(self, chunks: list, embeddings: list) -> bool:
        self.upsert_calls.append((chunks, embeddings))
        return True


def _fake_embeddings(texts: list[str]) -> list[list[float]]:
    """与文本数量一致的假向量（维度任意，upsert mock 不校验）。"""
    return [[0.1, 0.2, 0.3] for _ in texts]


async def test_seed_docs_qdrant_unavailable_returns_stats(monkeypatch):
    """Qdrant 不可用：返回统计 dict + qdrant_ok=False，不抛异常。"""
    qdrant = FakeQdrant(connected=False)
    monkeypatch.setattr("app.core.rag.seeder.get_qdrant", lambda: qdrant)
    monkeypatch.setattr("app.core.rag.seeder.embed_sync", _fake_embeddings)

    result = await seed_docs(DOCS, _metadata_fn, log_prefix="Test")

    assert result["docs"] == 2
    assert result["chunks"] == 0
    assert result["qdrant_ok"] is False
    assert qdrant.upsert_calls == []


async def test_seed_docs_upserts_chunks_and_builds_bm25(monkeypatch):
    """正常流程：每文档 chunk → upsert；BM25 用全量 chunk 重建。"""
    qdrant = FakeQdrant()
    bm25 = MagicMock()
    monkeypatch.setattr("app.core.rag.seeder.get_qdrant", lambda: qdrant)
    monkeypatch.setattr("app.core.rag.seeder.embed_sync", _fake_embeddings)
    monkeypatch.setattr("app.core.rag.seeder.get_bm25", lambda: bm25)

    result = await seed_docs(DOCS, _metadata_fn, log_prefix="Test")

    # 每个文档一次 upsert，chunk 数计入统计
    assert len(qdrant.upsert_calls) == 2
    total_upserted = sum(len(c) for c, _ in qdrant.upsert_calls)
    assert result["chunks"] == total_upserted > 0
    assert result["vector_written"] == result["chunks"]
    assert result["qdrant_ok"] is True
    # upsert 的 chunk 带文档元数据
    first_chunks, _ = qdrant.upsert_calls[0]
    assert first_chunks[0]["doc_id"] == "mall/business/pricing"
    assert first_chunks[0]["title"] == "价格优惠"
    # BM25 以全部 chunk 构建一次
    assert bm25.build.call_count == 1
    assert len(bm25.build.call_args[0][0]) == result["chunks"]
    assert qdrant.closed is True


async def test_seed_docs_embedding_failure_skips_doc(monkeypatch, caplog):
    """embedding 失败（返回 None）：跳过该文档，不中断其他文档。"""
    qdrant = FakeQdrant()
    bm25 = MagicMock()
    real_embed = _fake_embeddings

    def flaky_embed(texts: list[str]):
        # 首个文档（pricing）embedding 失败，第二个正常
        if texts and "价格优惠" in texts[0]:
            return None
        return real_embed(texts)

    monkeypatch.setattr("app.core.rag.seeder.get_qdrant", lambda: qdrant)
    monkeypatch.setattr("app.core.rag.seeder.embed_sync", flaky_embed)
    monkeypatch.setattr("app.core.rag.seeder.get_bm25", lambda: bm25)

    result = await seed_docs(DOCS, _metadata_fn, log_prefix="Test")

    # pricing 被跳过（upsert 仅 refund 一次），BM25 仍以全量构建
    assert len(qdrant.upsert_calls) == 1
    skipped_chunks, _ = qdrant.upsert_calls[0]
    assert skipped_chunks[0]["doc_id"] == "mall/business/refund"
    assert result["chunks"] == len(skipped_chunks)
    assert any("embedding 失败" in r.message for r in caplog.records)


async def test_seed_docs_reset_deletes_docs_first(monkeypatch):
    """reset 模式：每个文档先按 doc_id 清空再写入（幂等重建）。"""
    qdrant = FakeQdrant()
    bm25 = MagicMock()
    monkeypatch.setattr("app.core.rag.seeder.get_qdrant", lambda: qdrant)
    monkeypatch.setattr("app.core.rag.seeder.embed_sync", _fake_embeddings)
    monkeypatch.setattr("app.core.rag.seeder.get_bm25", lambda: bm25)

    await seed_docs(DOCS, _metadata_fn, reset=True, log_prefix="Test")

    assert set(qdrant.deleted) == {d["doc_id"] for d in DOCS}
    assert len(qdrant.upsert_calls) == 2


async def test_seed_docs_no_reset_keeps_old_docs(monkeypatch):
    """非 reset 模式：不调用 delete_by_doc（增量写入语义）。"""
    qdrant = FakeQdrant()
    monkeypatch.setattr("app.core.rag.seeder.get_qdrant", lambda: qdrant)
    monkeypatch.setattr("app.core.rag.seeder.embed_sync", _fake_embeddings)
    monkeypatch.setattr("app.core.rag.seeder.get_bm25", lambda: MagicMock())

    await seed_docs(DOCS, _metadata_fn, reset=False, log_prefix="Test")

    assert qdrant.deleted == []


# ---------- Qdrant upsert 确定性 id（幂等覆盖）----------

async def test_qdrant_upsert_deterministic_uuid5(monkeypatch):
    """upsert 用 uuid5(doc_id:chunk_index)：同文档重复 seed 得到相同 id（不翻倍）。"""
    from qdrant_client.http import models

    from app.core.infra import qdrant as qdrant_mod

    client = qdrant_mod.QdrantClient()
    fake_client = MagicMock()
    fake_client.upsert = AsyncMock()
    client._client = fake_client
    monkeypatch.setattr(qdrant_mod, "is_open", lambda name: False)

    async def fake_breaker(name, fn, *a, **kw):
        return await fn(*a, **kw)

    monkeypatch.setattr(qdrant_mod, "call_with_breaker", fake_breaker)

    chunks = [
        {"doc_id": "mall/business/pricing", "text": "满 300 减 30", "chunk_index": 0},
        {"doc_id": "mall/business/pricing", "text": "满 99 包邮", "chunk_index": 1},
    ]
    embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    await client.upsert(chunks, embeddings)
    await client.upsert(chunks, embeddings)

    assert fake_client.upsert.call_count == 2
    first_points: list[models.PointStruct] = fake_client.upsert.call_args_list[0].kwargs["points"]
    second_points: list[models.PointStruct] = fake_client.upsert.call_args_list[1].kwargs["points"]
    # 同 chunk 两次 seed 的 id 完全一致（幂等覆盖）；不同 chunk 的 id 不同
    assert [p.id for p in first_points] == [p.id for p in second_points]
    assert first_points[0].id != first_points[1].id
