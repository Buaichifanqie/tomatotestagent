from __future__ import annotations

from typing import TYPE_CHECKING

from testagent.common.errors import RAGSearchError
from testagent.common.logging import get_logger
from testagent.rag.vector_store import ChromaDBVectorStore

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings
    from testagent.rag.vector_store import IVectorStore

logger = get_logger(__name__)


class VectorStoreFactory:
    __test__ = False

    @staticmethod
    def create(settings: TestAgentSettings) -> IVectorStore:
        backend = settings.vector_store_backend.lower()

        if backend == "chromadb":
            return ChromaDBVectorStore(
                persist_dir=settings.chroma_persist_dir,
            )

        if backend == "milvus":
            try:
                from testagent.rag.milvus_store import MilvusVectorStore

                return MilvusVectorStore(
                    host=settings.milvus_host,
                    port=settings.milvus_port,
                    collection_prefix=settings.milvus_collection_prefix,
                )
            except RAGSearchError:
                raise
            except Exception as exc:
                raise RAGSearchError(
                    f"Failed to create MilvusVectorStore: {exc}",
                    code="MILVUS_CREATE_FAILED",
                    details={"host": settings.milvus_host, "port": settings.milvus_port},
                ) from exc

        raise RAGSearchError(
            f"Unknown vector store backend: {backend!r}",
            code="UNKNOWN_VECTOR_STORE_BACKEND",
            details={"backend": backend, "available": ["chromadb", "milvus"]},
        )
