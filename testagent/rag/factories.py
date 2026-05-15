from __future__ import annotations

from typing import TYPE_CHECKING

from testagent.rag.embedding import EmbeddingFactory
from testagent.rag.fulltext import MeilisearchFullText
from testagent.rag.pipeline import RAGPipeline
from testagent.rag.reranker import RerankerFactory
from testagent.rag.vector_store_factory import VectorStoreFactory

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings


def create_pipeline(settings: TestAgentSettings) -> RAGPipeline:
    embedding_service = EmbeddingFactory.create(settings)
    vector_store = VectorStoreFactory.create(settings)
    fulltext = MeilisearchFullText(
        url=settings.meilisearch_url,
        api_key=settings.meilisearch_api_key.get_secret_value(),
    )
    reranker = RerankerFactory.create(
        reranker_enabled=settings.reranker_enabled,
        reranker_model=settings.reranker_model,
    )
    return RAGPipeline(
        embedding_service=embedding_service,
        vector_store=vector_store,
        fulltext=fulltext,
        reranker=reranker,
    )
