from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from testagent.common.logging import get_logger
from testagent.rag.fusion import rrf_fusion
from testagent.rag.ingestion import Chunk, DocumentIngestor, TextChunker

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
    ) -> None:
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._fulltext = fulltext
        self._ingestor = DocumentIngestor()
        self._text_chunker = TextChunker()

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
        embeddings = await self._embedding_service.embed_batch(texts)

        vector_docs: list[dict[str, Any]] = []
        fulltext_docs: list[dict[str, Any]] = []

        for chunk, embedding in zip(chunks, embeddings, strict=True):
            vector_docs.append(
                {
                    "id": chunk.chunk_id,
                    "embedding": embedding,
                    "metadata": dict(chunk.metadata),
                    "document": chunk.content,
                }
            )
            fulltext_docs.append(
                {
                    "id": chunk.chunk_id,
                    "document": chunk.content,
                    "metadata": dict(chunk.metadata),
                }
            )

        await self._vector_store.upsert(vector_docs)
        await self._fulltext.index(fulltext_docs)

        logger.info(
            "Ingested %d chunks into collection '%s' from %s",
            len(chunks),
            collection,
            source,
            extra={
                "extra_data": {
                    "chunk_count": len(chunks),
                    "collection": collection,
                    "source": source,
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
        query_filters: dict[str, Any] = {"collection": collection}
        if filters:
            query_filters.update(filters)

        query_vector = await self._embedding_service.embed(query_text)

        vector_results = await self._vector_store.search(
            query_vector=query_vector,
            top_k=top_k * 2,
            filters=query_filters,
        )

        keyword_results = await self._fulltext.search(
            query=query_text,
            top_k=top_k * 2,
            filters=query_filters,
        )

        fused = rrf_fusion(vector_results, keyword_results, k=60)
        fused = fused[:top_k]

        return [
            RAGResult(
                doc_id=str(r["id"]),
                content=str(r.get("document", "")),
                score=float(r.get("score", 0.0)),
                metadata=dict(r.get("metadata", {})),
            )
            for r in fused
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
        embeddings = await self._embedding_service.embed_batch(texts)

        vector_docs: list[dict[str, Any]] = []
        fulltext_docs: list[dict[str, Any]] = []

        for chunk, embedding in zip(result_chunks, embeddings, strict=True):
            vector_docs.append(
                {
                    "id": chunk.chunk_id,
                    "embedding": embedding,
                    "metadata": dict(chunk.metadata),
                    "document": chunk.content,
                }
            )
            fulltext_docs.append(
                {
                    "id": chunk.chunk_id,
                    "document": chunk.content,
                    "metadata": dict(chunk.metadata),
                }
            )

        await self._vector_store.upsert(vector_docs)
        await self._fulltext.index(fulltext_docs)

        logger.info(
            "Write-back %d chunks into collection '%s'",
            len(result_chunks),
            collection,
            extra={
                "extra_data": {
                    "chunk_count": len(result_chunks),
                    "collection": collection,
                    "doc_id": doc_id,
                }
            },
        )
