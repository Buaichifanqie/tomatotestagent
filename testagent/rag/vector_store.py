from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

import chromadb

from testagent.common.errors import RAGSearchError
from testagent.common.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_COLLECTION = "default"
_DEFAULT_TOP_K = 10


@runtime_checkable
class IVectorStore(Protocol):
    async def upsert(self, docs: list[dict[str, Any]]) -> None: ...
    async def search(
        self,
        query_vector: list[float],
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...
    async def delete(self, doc_ids: list[str]) -> None: ...


class ChromaDBVectorStore:
    __test__ = False

    def __init__(self, persist_dir: str, collection_name: str = _DEFAULT_COLLECTION) -> None:
        self._persist_dir = persist_dir
        self._collection_name = collection_name
        self._client: Any
        self._collection: Any
        self._client, self._collection = self._init_sync()

    def _init_sync(self) -> tuple[Any, Any]:
        client = chromadb.PersistentClient(
            path=self._persist_dir,
        )
        collection = client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        return client, collection

    async def upsert(self, docs: list[dict[str, Any]]) -> None:
        if not docs:
            logger.warning("upsert called with empty docs list")
            return

        ids: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict[str, Any]] = []
        documents: list[str] = []

        for doc in docs:
            doc_id = doc.get("id")
            if doc_id is None:
                logger.warning("Skipping doc without 'id' field")
                continue
            ids.append(str(doc_id))
            embeddings.append(doc.get("embedding", []))
            metadatas.append(doc.get("metadata", {}))
            documents.append(doc.get("document", ""))

        if not ids:
            return

        loop = asyncio.get_running_loop()

        def _upsert_sync() -> None:
            self._collection.upsert(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
                documents=documents,
            )

        try:
            await loop.run_in_executor(None, _upsert_sync)
            logger.info("Upserted %d docs into ChromaDB collection '%s'", len(ids), self._collection_name)
        except Exception as exc:
            raise RAGSearchError(
                f"ChromaDB upsert failed: {exc}",
                code="CHROMA_UPSERT_ERROR",
                details={"collection": self._collection_name, "doc_count": len(ids)},
            ) from exc

    async def search(
        self,
        query_vector: list[float],
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()

        def _query_sync() -> Any:
            where_clause: dict[str, Any] | None = None
            if filters is not None:
                where_clause = {}
                for key, value in filters.items():
                    if isinstance(value, list):
                        where_clause[key] = {"$in": value}
                    else:
                        where_clause[key] = value
            return self._collection.query(
                query_embeddings=[query_vector],
                n_results=top_k,
                where=where_clause,
            )

        try:
            result = await loop.run_in_executor(None, _query_sync)
        except Exception as exc:
            raise RAGSearchError(
                f"ChromaDB search failed: {exc}",
                code="CHROMA_SEARCH_ERROR",
                details={"collection": self._collection_name, "top_k": top_k},
            ) from exc

        return self._format_results(result)

    @staticmethod
    def _format_results(raw: dict[str, Any]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        ids = raw.get("ids", [[]])[0]
        distances = raw.get("distances", [[]])[0]
        metadatas = raw.get("metadatas", [[]])[0]
        documents = raw.get("documents", [[]])[0]

        for i in range(len(ids)):
            formatted.append(
                {
                    "id": ids[i],
                    "score": float(1.0 - distances[i]) if i < len(distances) else 0.0,
                    "metadata": metadatas[i] if i < len(metadatas) else {},
                    "document": documents[i] if i < len(documents) else "",
                }
            )

        return formatted

    async def delete(self, doc_ids: list[str]) -> None:
        if not doc_ids:
            logger.warning("delete called with empty doc_ids list")
            return

        loop = asyncio.get_running_loop()

        def _delete_sync() -> None:
            self._collection.delete(ids=doc_ids)

        try:
            await loop.run_in_executor(None, _delete_sync)
            logger.info("Deleted %d docs from ChromaDB collection '%s'", len(doc_ids), self._collection_name)
        except Exception as exc:
            raise RAGSearchError(
                f"ChromaDB delete failed: {exc}",
                code="CHROMA_DELETE_ERROR",
                details={"collection": self._collection_name, "doc_ids": doc_ids},
            ) from exc
