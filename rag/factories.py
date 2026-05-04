from __future__ import annotations

from typing import TYPE_CHECKING

from testagent.rag.embedding import LocalEmbeddingService
from testagent.rag.fulltext import MeilisearchFullText
from testagent.rag.pipeline import RAGPipeline
from testagent.rag.vector_store import ChromaDBVectorStore

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings


def create_pipeline(settings: TestAgentSettings) -> RAGPipeline:
    embedding_service = LocalEmbeddingService(model_name=settings.embedding_model)
    vector_store = ChromaDBVectorStore(persist_dir=settings.chroma_persist_dir)
    fulltext = MeilisearchFullText(
        url=settings.meilisearch_url,
        api_key=settings.meilisearch_api_key.get_secret_value(),
    )
    return RAGPipeline(
        embedding_service=embedding_service,
        vector_store=vector_store,
        fulltext=fulltext,
    )
