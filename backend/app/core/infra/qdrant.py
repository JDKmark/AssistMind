"""Qdrant 客户端：collection 管理 + upsert + search + RBAC filter + 失败降级。

RBAC：通过 payload filter 按 security_group 字段过滤，不建独立权限表。
失败降级：
- 连接失败：is_connected=False，调用方降级为仅 BM25 召回
- search/upsert 失败：通过断路器计数，返回空结果/False
- 断路器 Open：直接返回空结果，避免无谓调用
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from app.config import get_settings
from app.core.infra.circuit_breaker import (
    CircuitBreakerOpenError,
    call_with_breaker,
    is_open,
)

logger = logging.getLogger(__name__)
settings = get_settings()


class QdrantClient:
    """Qdrant 异步客户端。"""

    def __init__(self) -> None:
        self._client: AsyncQdrantClient | None = None

    async def connect(self) -> None:
        try:
            self._client = AsyncQdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY,
                timeout=settings.QDRANT_TIMEOUT,
                # 客户端 1.18 与 server 1.12 主版本不一致，关闭兼容性检查
                check_compatibility=False,
            )
            await self._ensure_collection()
            logger.info("[Qdrant] 连接成功，collection=%s", settings.QDRANT_COLLECTION)
        except Exception as e:
            self._client = None
            logger.warning("[Qdrant] 连接失败（将降级为仅 BM25）: %s", e)

    async def _ensure_collection(self) -> None:
        if not self._client:
            return
        collections = await self._client.get_collections()
        names = [c.name for c in collections.collections]
        if settings.QDRANT_COLLECTION not in names:
            await self._client.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config=models.VectorParams(
                    size=settings.EMBEDDING_DIMENSION,
                    distance=models.Distance.COSINE,
                ),
            )
            logger.info("[Qdrant] 创建 collection: %s", settings.QDRANT_COLLECTION)

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    async def upsert(
        self,
        chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> bool:
        """批量写入向量。"""
        if not self._client or len(chunks) != len(embeddings):
            return False
        if is_open("qdrant"):
            logger.warning("[Qdrant] 断路器 Open，跳过 upsert")
            return False
        try:
            points = [
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=emb,
                    payload={
                        "text": c["text"],
                        "doc_id": c["doc_id"],
                        "title": c.get("title", ""),
                        "source": c.get("source", ""),
                        "category": c.get("category", ""),
                        "security_group": c.get("security_group", ["user", "agent", "admin"]),
                        "section_title": c.get("section_title", ""),
                        "table_comment": c.get("table_comment", ""),
                    },
                )
                for c, emb in zip(chunks, embeddings)
            ]
            await call_with_breaker(
                "qdrant",
                self._client.upsert,
                collection_name=settings.QDRANT_COLLECTION,
                points=points,
            )
            return True
        except CircuitBreakerOpenError:
            logger.warning("[Qdrant] upsert 断路器 Open")
            return False
        except Exception as e:
            logger.warning("[Qdrant] upsert 失败: %s", e)
            return False

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 40,
        role: str = "user",
    ) -> list[dict[str, Any]]:
        """向量召回，含 RBAC 过滤。

        断路器 Open 时直接返回 []，避免无谓调用。
        """
        if not self._client:
            return []
        if is_open("qdrant"):
            logger.warning("[Qdrant] 断路器 Open，跳过 search")
            return []
        try:
            flt = models.FieldCondition(
                key="security_group",
                match=models.MatchAny(any=[role]),
            )

            async def _do_search() -> list[dict[str, Any]]:
                # qdrant-client >=1.12 推荐 query_points（1.18 已移除 search）
                response = await self._client.query_points(  # type: ignore[union-attr]
                    collection_name=settings.QDRANT_COLLECTION,
                    query=query_vector,
                    limit=top_k,
                    query_filter=models.Filter(must=[flt]),
                    with_payload=True,
                )
                results = response.points
                return [
                    {
                        "id": str(r.id),
                        "score": float(r.score),
                        "text": r.payload.get("text", ""),
                        "doc_id": r.payload.get("doc_id", ""),
                        "title": r.payload.get("title", ""),
                        "source": r.payload.get("source", ""),
                        "section_title": r.payload.get("section_title", ""),
                        "table_comment": r.payload.get("table_comment", ""),
                    }
                    for r in results
                ]

            return await call_with_breaker("qdrant", _do_search)
        except CircuitBreakerOpenError:
            logger.warning("[Qdrant] search 断路器 Open")
            return []
        except Exception as e:
            logger.warning("[Qdrant] search 失败（将降级为仅 BM25）: %s", e)
            return []

    async def delete_by_doc(self, doc_id: str) -> bool:
        """按 doc_id 删除所有 chunk。"""
        if not self._client:
            return False
        if is_open("qdrant"):
            return False
        try:
            await call_with_breaker(
                "qdrant",
                self._client.delete,
                collection_name=settings.QDRANT_COLLECTION,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                    )
                ),
            )
            return True
        except CircuitBreakerOpenError:
            return False
        except Exception as e:
            logger.warning("[Qdrant] delete_by_doc 失败: %s", e)
            return False

    async def scroll_all(self) -> list[dict[str, Any]]:
        """全量拉取所有 chunk（含 payload）。用于服务启动时构建 BM25 内存索引。"""
        if not self._client:
            return []
        if is_open("qdrant"):
            logger.warning("[Qdrant] 断路器 Open，跳过 scroll_all")
            return []
        try:
            async def _do_scroll() -> list[dict[str, Any]]:
                # qdrant-client 1.18：scroll 返回 (points, next_page_offset) 元组
                points = []
                offset = None
                while True:
                    resp_points, next_offset = await self._client.scroll(
                        collection_name=settings.QDRANT_COLLECTION,
                        limit=1000,
                        offset=offset,
                        with_payload=True,
                        with_vectors=False,
                    )
                    for p in resp_points:
                        payload = p.payload or {}
                        points.append(
                            {
                                "doc_id": payload.get("doc_id", ""),
                                "title": payload.get("title", ""),
                                "source": payload.get("source", ""),
                                "text": payload.get("text", ""),
                                "security_group": payload.get(
                                    "security_group", ["user", "agent", "admin"]
                                ),
                                "section_title": payload.get("section_title", ""),
                                "table_comment": payload.get("table_comment", ""),
                            }
                        )
                    if next_offset is None:
                        break
                    offset = next_offset
                return points

            return await call_with_breaker("qdrant", _do_scroll)
        except CircuitBreakerOpenError:
            logger.warning("[Qdrant] scroll_all 断路器 Open")
            return []
        except Exception as e:
            logger.warning("[Qdrant] scroll_all 失败: %s", e)
            return []

    async def count(self) -> int:
        if not self._client:
            return 0
        if is_open("qdrant"):
            return 0
        try:
            r = await call_with_breaker(
                "qdrant",
                self._client.count,
                collection_name=settings.QDRANT_COLLECTION,
                exact=True,
            )
            return r.count
        except CircuitBreakerOpenError:
            return 0
        except Exception as e:
            logger.warning("[Qdrant] count 失败: %s", e)
            return 0

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


_qdrant: QdrantClient | None = None


def get_qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient()
    return _qdrant
