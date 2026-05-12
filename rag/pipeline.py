from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from testagent.common.errors import RAGDegradedError, RAGSearchError
from testagent.common.logging import get_logger
from testagent.rag.fusion import rrf_fusion
from testagent.rag.ingestion import Chunk, DocumentIngestor, TextChunker
from testagent.rag.reranker import IReranker, NoopReranker

if TYPE_CHECKING:
    from testagent.rag.embedding import IEmbeddingService
    from testagent.rag.fulltext import IFullTextSearch
    from testagent.rag.vector_store import IVectorStore

logger = get_logger(__name__)


@dataclass
class RAGResult:
    doc_id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class RAGPipeline:
    __test__ = False

    def __init__(
        self,
        embedding_service: IEmbeddingService,
        vector_store: IVectorStore,
        fulltext: IFullTextSearch,
        reranker: IReranker | None = None,
    ) -> None:
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._fulltext = fulltext
        self._reranker: IReranker = reranker or NoopReranker()
        self._ingestor = DocumentIngestor()
        self._text_chunker = TextChunker()

    @property
    def is_embedding_degraded(self) -> bool:
        if hasattr(self._embedding_service, "is_degraded"):
            return bool(self._embedding_service.is_degraded)
        return False

    async def _safe_embed(
        self,
        texts: list[str],
    ) -> list[list[float]] | None:
        try:
            return await self._embedding_service.embed_batch(texts)
        except RAGDegradedError:
            logger.warning(
                "Embedding service degraded, vector search will be skipped",
                extra={"extra_data": {"text_count": len(texts)}},
            )
            return None
        except Exception as exc:
            logger.warning(
                "Embedding service failed unexpectedly: %s, vector search will be skipped",
                exc,
                exc_info=exc,
                extra={"extra_data": {"error": str(exc)}},
            )
            return None

    async def _safe_embed_single(self, text: str) -> list[float] | None:
        try:
            return await self._embedding_service.embed(text)
        except RAGDegradedError:
            logger.warning(
                "Embedding service degraded for single query, vector search will be skipped"
            )
            return None
        except Exception as exc:
            logger.warning(
                "Embedding service failed unexpectedly for single query: %s, vector search will be skipped",
                exc,
                exc_info=exc,
            )
            return None

    async def _safe_vector_search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        try:
            return await self._vector_store.search(
                query_vector=query_vector,
                top_k=top_k,
                filters=filters,
            )
        except RAGSearchError as exc:
            logger.warning(
                "Vector store search failed: %s, falling back to BM25 only",
                exc,
                extra={"extra_data": {"code": exc.code, "details": exc.details}},
            )
            return None
        except Exception as exc:
            logger.warning(
                "Vector store search failed unexpectedly: %s, falling back to BM25 only",
                exc,
                exc_info=exc,
            )
            return None

    async def ingest(
        self,
        source: str,
        collection: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        chunks = await self._ingestor.ingest(source, collection, metadata)
        if not chunks:
            return 0

        texts = [chunk.content for chunk in chunks]
        embeddings = await self._safe_embed(texts)

        fulltext_docs: list[dict[str, Any]] = []
        for chunk in chunks:
            fulltext_docs.append(
                {
                    "id": chunk.chunk_id,
                    "document": chunk.content,
                    "metadata": dict(chunk.metadata),
                }
            )

        await self._fulltext.index(fulltext_docs)

        if embeddings is not None:
            vector_docs: list[dict[str, Any]] = []
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                vector_docs.append(
                    {
                        "id": chunk.chunk_id,
                        "embedding": embedding,
                        "metadata": dict(chunk.metadata),
                        "document": chunk.content,
                    }
                )
            await self._vector_store.upsert(vector_docs)
            logger.info(
                "Ingested %d chunks into collection '%s' from %s (vector + fulltext)",
                len(chunks),
                collection,
                source,
                extra={
                    "extra_data": {
                        "chunk_count": len(chunks),
                        "collection": collection,
                        "source": source,
                        "mode": "vector_and_fulltext",
                    }
                },
            )
        else:
            logger.info(
                "Ingested %d chunks into collection '%s' from %s (fulltext only, vector degraded)",
                len(chunks),
                collection,
                source,
                extra={
                    "extra_data": {
                        "chunk_count": len(chunks),
                        "collection": collection,
                        "source": source,
                        "mode": "fulltext_only_degraded",
                    }
                },
            )

        return len(chunks)

    async def query(
        self,
        query_text: str,
        collection: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[RAGResult]:
        start_time = time.monotonic()

        query_filters: dict[str, Any] = {"collection": collection}
        if filters:
            query_filters.update(filters)

        query_vector = await self._safe_embed_single(query_text)
        embed_degraded = query_vector is None

        vector_results: list[dict[str, Any]] = []
        if query_vector is not None:
            vector_raw = await self._safe_vector_search(
                query_vector=query_vector,
                top_k=top_k * 2,
                filters=query_filters,
            )
            if vector_raw is not None:
                vector_results = vector_raw
                logger.debug(
                    "Vector search returned %d results",
                    len(vector_results),
                    extra={
                        "extra_data": {
                            "collection": collection,
                            "result_count": len(vector_results),
                            "mode": "vector",
                        }
                    },
                )
            else:
                logger.info(
                    "Vector search unavailable for collection '%s', using BM25 only",
                    collection,
                    extra={"extra_data": {"collection": collection}},
                )
        else:
            logger.info(
                "Embedding unavailable for collection '%s', using BM25 only",
                collection,
                extra={"extra_data": {"collection": collection}},
            )

        keyword_results: list[dict[str, Any]] = []
        try:
            keyword_results = await self._fulltext.search(
                query=query_text,
                top_k=top_k * 2,
                filters=query_filters,
            )
            logger.debug(
                "Keyword search returned %d results",
                len(keyword_results),
                extra={
                    "extra_data": {
                        "collection": collection,
                        "result_count": len(keyword_results),
                        "mode": "fulltext",
                    }
                },
            )
        except Exception as exc:
            logger.warning(
                "Fulltext search failed: %s",
                exc,
                exc_info=exc,
                extra={"extra_data": {"collection": collection}},
            )

        if vector_results and keyword_results:
            fused = rrf_fusion(vector_results, keyword_results, k=60)
            logger.debug(
                "RRF fusion combined %d vector + %d keyword results into %d",
                len(vector_results),
                len(keyword_results),
                len(fused),
            )
        elif vector_results:
            fused = vector_results[: top_k * 2]
            logger.debug("Only vector results available (%d documents)", len(fused))
        elif keyword_results:
            fused = keyword_results[: top_k * 2]
            logger.debug("Only keyword results available (%d documents)", len(fused))
        else:
            logger.warning(
                "No results from either vector or keyword search for collection '%s'",
                collection,
                extra={"extra_data": {"collection": collection}},
            )
            if embed_degraded:
                raise RAGDegradedError(
                    "RAG query failed for collection",
                    code="RAG_QUERY_FAILED",
                )
            return []

        reranked = await self._reranker.rerank(
            query=query_text,
            documents=fused,
            top_k=top_k,
        )

        elapsed = time.monotonic() - start_time
        logger.info(
            "RAG query on '%s' returned %d results in %.3fs",
            collection,
            len(reranked),
            elapsed,
            extra={
                "extra_data": {
                    "collection": collection,
                    "top_k": top_k,
                    "result_count": len(reranked),
                    "latency_seconds": round(elapsed, 4),
                    "vector_results": len(vector_results),
                    "keyword_results": len(keyword_results),
                    "reranker": type(self._reranker).__name__,
                }
            },
        )

        return [
            RAGResult(
                doc_id=str(r["id"]),
                content=str(r.get("document", r.get("content", ""))),
                score=float(r.get("rerank_score", r.get("score", 0.0))),
                metadata=dict(r.get("metadata", {})),
            )
            for r in reranked
        ]

    async def write_back(
        self,
        content: str,
        collection: str,
        metadata: dict[str, Any],
    ) -> None:
        raw_chunks = self._text_chunker.chunk(content)
        doc_id_input = f"{collection}:write_back:{hashlib.sha256(content.encode()).hexdigest()}"
        doc_id = hashlib.sha256(doc_id_input.encode()).hexdigest()[:16]

        base_meta = dict(metadata)
        base_meta["collection"] = collection
        base_meta["source"] = "write_back"
        base_meta["write_back_at"] = ""

        result_chunks: list[Chunk] = []
        for i, chunk in enumerate(raw_chunks):
            chunk.doc_id = doc_id
            chunk.chunk_id = f"{doc_id}_wb_{i:04d}"
            chunk.metadata = {**base_meta, **chunk.metadata}
            result_chunks.append(chunk)

        texts = [chunk.content for chunk in result_chunks]
        embeddings = await self._safe_embed(texts)

        fulltext_docs: list[dict[str, Any]] = []
        for chunk in result_chunks:
            fulltext_docs.append(
                {
                    "id": chunk.chunk_id,
                    "document": chunk.content,
                    "metadata": dict(chunk.metadata),
                }
            )
        await self._fulltext.index(fulltext_docs)

        if embeddings is not None:
            vector_docs: list[dict[str, Any]] = []
            for chunk, embedding in zip(result_chunks, embeddings, strict=True):
                vector_docs.append(
                    {
                        "id": chunk.chunk_id,
                        "embedding": embedding,
                        "metadata": dict(chunk.metadata),
                        "document": chunk.content,
                    }
                )
            await self._vector_store.upsert(vector_docs)
            logger.info(
                "Write-back %d chunks into collection '%s' (vector + fulltext)",
                len(result_chunks),
                collection,
                extra={
                    "extra_data": {
                        "chunk_count": len(result_chunks),
                        "collection": collection,
                        "doc_id": doc_id,
                        "mode": "vector_and_fulltext",
                    }
                },
            )
        else:
            logger.info(
                "Write-back %d chunks into collection '%s' (fulltext only, vector degraded)",
                len(result_chunks),
                collection,
                extra={
                    "extra_data": {
                        "chunk_count": len(result_chunks),
                        "collection": collection,
                        "doc_id": doc_id,
                        "mode": "fulltext_only_degraded",
                    }
                },
            )

    async def health_check(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "vector_store": False,
            "fulltext": False,
            "embedding": False,
            "reranker": False,
        }

        try:
            await self._vector_store.search(
                query_vector=[0.0] * 8,
                top_k=1,
            )
            status["vector_store"] = True
        except Exception:
            status["vector_store"] = False

        try:
            await self._fulltext.search(query="health", top_k=1)
            status["fulltext"] = True
        except Exception:
            status["fulltext"] = False

        emb_test = await self._safe_embed_single("health check")
        status["embedding"] = emb_test is not None

        try:
            rerank_test = await self._reranker.rerank(
                query="health",
                documents=[{"id": "0", "content": "health check document"}],
                top_k=1,
            )
            status["reranker"] = len(rerank_test) > 0
        except Exception:
            status["reranker"] = False

        status["degraded"] = not status["embedding"] or not status["vector_store"]

        logger.info(
            "RAG pipeline health check: %s",
            status,
            extra={"extra_data": {"health_status": status}},
        )
        return status
