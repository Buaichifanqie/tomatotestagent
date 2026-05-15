from __future__ import annotations

import contextlib
import os
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from testagent.common.errors import RAGDegradedError, RAGSearchError
from testagent.rag.embedding import EmbeddingFailover, IEmbeddingService, SimpleEmbeddingService
from testagent.rag.fulltext import IFullTextSearch
from testagent.rag.pipeline import RAGPipeline
from testagent.rag.reranker import CrossEncoderReranker, NoopReranker
from testagent.rag.vector_store import IVectorStore

MILVUS_HOST = os.environ.get("TESTAGENT_MILVUS_HOST", "localhost")
MILVUS_PORT = int(os.environ.get("TESTAGENT_MILVUS_PORT", "19530"))
MEILI_HOST = os.environ.get("TESTAGENT_MEILI_HOST", "localhost")
MEILI_PORT = int(os.environ.get("TESTAGENT_MEILI_PORT", "7700"))
MEILI_URL = f"http://{MEILI_HOST}:{MEILI_PORT}"
MEILI_API_KEY = os.environ.get("TESTAGENT_MEILI_API_KEY", "testagent-dev-master-key")

TEST_DIMENSION = 8
TEST_COLLECTION = "test_v1_pipeline"
TEST_SAMPLE_DIR = Path(__file__).resolve().parent.parent / "_test_data" / "rag_v1_samples"


def milvus_is_available() -> bool:
    try:
        with socket.create_connection((MILVUS_HOST, MILVUS_PORT), timeout=2):
            return True
    except OSError:
        return False


def meilisearch_is_available() -> bool:
    try:
        with socket.create_connection((MEILI_HOST, MEILI_PORT), timeout=2):
            return True
    except OSError:
        return False


requires_milvus = pytest.mark.skipif(
    not milvus_is_available(),
    reason="Milvus not available; set TESTAGENT_MILVUS_HOST and ensure Milvus is running",
)
requires_meili = pytest.mark.skipif(
    not meilisearch_is_available(),
    reason="Meilisearch not available; set TESTAGENT_MEILI_HOST and ensure Meilisearch is running",
)
requires_both = pytest.mark.skipif(
    not (milvus_is_available() and meilisearch_is_available()),
    reason="Both Milvus and Meilisearch are required",
)


def _generate_embedding(dim: int = TEST_DIMENSION) -> list[float]:
    return [0.1 * (i + 1) for i in range(dim)]


async def _create_milvus_store() -> IVectorStore:
    from testagent.rag.milvus_store import MilvusVectorStore

    return MilvusVectorStore(
        host=MILVUS_HOST,
        port=MILVUS_PORT,
        collection_prefix="testagent_v1_",
    )


async def _create_meili_fulltext() -> IFullTextSearch:
    from testagent.rag.fulltext import MeilisearchFullText

    return MeilisearchFullText(
        url=MEILI_URL,
        api_key=MEILI_API_KEY,
        index_name=TEST_COLLECTION,
    )


async def _cleanup_milvus_collections(prefix: str = "testagent_v1_") -> None:
    try:
        from pymilvus import MilvusClient  # type: ignore[import-untyped]

        client = MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}")
        full_name = f"{prefix}default"
        with contextlib.suppress(Exception):
            client.drop_collection(full_name)
        client.close()
    except Exception:
        pass


async def _cleanup_meili_index() -> None:
    try:
        from meilisearch_python_async import Client as MeiliClient

        async with MeiliClient(MEILI_URL, MEILI_API_KEY) as client:
            with contextlib.suppress(Exception):
                await client.delete_index(TEST_COLLECTION)  # type: ignore[attr-defined]
    except Exception:
        pass


TEST_SAMPLE_TEXT = """
RAG Pipeline V1.0 TestAgent Integration Test
=============================================
This document describes the RAG pipeline integration test for V1.0.
The pipeline supports dual-write to Milvus vector store and Meilisearch fulltext search.
It uses RRF fusion to combine results from both retrieval methods.
Cross-Encoder reranking is supported for improved precision.
When vector store is unavailable, the pipeline degrades to pure BM25 search.
The query latency target for V1.0 is under 1 second.
Write-back enables analysis results to be stored and retrieved later.
"""


@pytest_asyncio.fixture(autouse=True)
async def _cleanup() -> AsyncGenerator[None, None]:
    await _cleanup_milvus_collections()
    await _cleanup_meili_index()
    yield
    await _cleanup_milvus_collections()
    await _cleanup_meili_index()


async def _build_pipeline(
    embedding_service: IEmbeddingService | None = None,
    vector_store: IVectorStore | None = None,
    fulltext: IFullTextSearch | None = None,
    reranker_enabled: bool = False,
) -> RAGPipeline:
    if embedding_service is None:
        embedding_service = SimpleEmbeddingService(dimension=TEST_DIMENSION)
        embedding_service = EmbeddingFailover(primary=embedding_service, fallback=None)

    if vector_store is None:
        vector_store = await _create_milvus_store()

    if fulltext is None:
        fulltext = await _create_meili_fulltext()

    reranker = CrossEncoderReranker() if reranker_enabled else NoopReranker()

    return RAGPipeline(
        embedding_service=embedding_service,
        vector_store=vector_store,
        fulltext=fulltext,
        reranker=reranker,
    )


class TestRAGPipelineV1Milvus:
    @requires_both
    @pytest.mark.integration
    async def test_rag_pipeline_milvus(self) -> None:
        pipeline = await _build_pipeline()

        source_file = TEST_SAMPLE_DIR / "api_docs_sample.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(TEST_SAMPLE_TEXT, encoding="utf-8")

        try:
            chunk_count = await pipeline.ingest(
                source=str(source_file),
                collection=TEST_COLLECTION,
                metadata={"source_type": "test"},
            )
            assert chunk_count > 0, "Ingest should return positive chunk count"

            results = await pipeline.query(
                query_text="RRF fusion query",
                collection=TEST_COLLECTION,
                top_k=3,
            )
            assert len(results) > 0, "Query should return results after ingest"
            assert all(isinstance(r.score, float) for r in results)
            assert all(r.doc_id for r in results)
            assert all(r.content for r in results)

            has_rag_result = any(
                "RRF" in r.content or "fusion" in r.content or "reranking" in r.content for r in results
            )
            assert has_rag_result, (
                f"Results should contain relevant content about the query. "
                f"Contents: {[r.content[:80] for r in results]}"
            )
        finally:
            source_file.unlink(missing_ok=True)

    @requires_both
    @pytest.mark.integration
    async def test_rag_pipeline_with_reranker(self) -> None:
        try:
            _check = CrossEncoderReranker()
            _model = _check._ensure_model()
        except Exception:
            pytest.skip("CrossEncoder model (BAAI/bge-reranker-large) not available in this environment")

        no_reranker_pipeline = await _build_pipeline(reranker_enabled=False)
        reranker_pipeline = await _build_pipeline(reranker_enabled=True)

        source_file = TEST_SAMPLE_DIR / "reranker_test_sample.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(TEST_SAMPLE_TEXT, encoding="utf-8")

        try:
            chunk_count = await no_reranker_pipeline.ingest(
                source=str(source_file),
                collection=TEST_COLLECTION,
                metadata={"source_type": "reranker_test"},
            )
            assert chunk_count > 0

            results_no_reranker = await no_reranker_pipeline.query(
                query_text="BM25 fulltext search degradation",
                collection=TEST_COLLECTION,
                top_k=3,
            )
            assert len(results_no_reranker) > 0

            results_with_reranker = await reranker_pipeline.query(
                query_text="BM25 fulltext search degradation",
                collection=TEST_COLLECTION,
                top_k=3,
            )
            assert len(results_with_reranker) > 0

            no_rerank_scores = [r.score for r in results_no_reranker]
            rerank_scores = [r.score for r in results_with_reranker]

            top_no_rerank = max(no_rerank_scores) if no_rerank_scores else 0.0
            top_rerank = max(rerank_scores) if rerank_scores else 0.0

            assert top_rerank >= 0.0
            assert top_no_rerank >= 0.0

            has_relevant_reranker = any(
                "degradation" in r.content.lower() or "bm25" in r.content.lower() for r in results_with_reranker
            )
            assert has_relevant_reranker, (
                f"Reranked results should contain relevant documents. "
                f"Contents: {[r.content[:80] for r in results_with_reranker]}"
            )
        finally:
            source_file.unlink(missing_ok=True)

    @requires_meili
    @pytest.mark.integration
    async def test_rag_degradation_to_bm25(self) -> None:
        mock_vector_store = MagicMock(spec=IVectorStore)
        mock_vector_store.search = AsyncMock(
            side_effect=RAGSearchError(
                "Milvus connection refused",
                code="MILVUS_CONNECTION_FAILED",
            )
        )
        mock_vector_store.upsert = AsyncMock()
        mock_vector_store.delete = AsyncMock()

        fulltext = await _create_meili_fulltext()
        pipeline = await _build_pipeline(
            vector_store=mock_vector_store,
            fulltext=fulltext,
        )

        source_file = TEST_SAMPLE_DIR / "degradation_test_sample.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(TEST_SAMPLE_TEXT, encoding="utf-8")

        try:
            chunk_count = await pipeline.ingest(
                source=str(source_file),
                collection=TEST_COLLECTION,
                metadata={"source_type": "degradation_test"},
            )
            assert chunk_count > 0, "Ingest should succeed even with vector store failure"

            results = await pipeline.query(
                query_text="BM25 fulltext search",
                collection=TEST_COLLECTION,
                top_k=3,
            )
            assert len(results) > 0, "Query should still return BM25 results when vector store fails"
            assert all(r.score >= 0.0 for r in results)
            assert any("BM25" in r.content or "bm25" in r.content or "degradation" in r.content for r in results), (
                f"Degraded results should contain BM25-relevant content. Contents: {[r.content[:80] for r in results]}"
            )
        finally:
            source_file.unlink(missing_ok=True)

    @requires_both
    @pytest.mark.integration
    async def test_rag_latency_under_1s(self) -> None:
        pipeline = await _build_pipeline()

        source_file = TEST_SAMPLE_DIR / "latency_test_sample.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(TEST_SAMPLE_TEXT, encoding="utf-8")

        try:
            chunk_count = await pipeline.ingest(
                source=str(source_file),
                collection=TEST_COLLECTION,
                metadata={"source_type": "latency_test"},
            )
            assert chunk_count > 0

            measurement_count = 3
            latencies: list[float] = []

            for i in range(measurement_count):
                start = time.monotonic()
                results = await pipeline.query(
                    query_text="RAG pipeline V1.0 latency target",
                    collection=TEST_COLLECTION,
                    top_k=3,
                )
                elapsed = time.monotonic() - start
                latencies.append(elapsed)
                assert len(results) > 0, f"Query iteration {i} should return results"

            avg_latency = sum(latencies) / len(latencies)
            max_latency = max(latencies)

            assert avg_latency < 1.0, (
                f"Average RAG query latency {avg_latency:.3f}s exceeds 1.0s target. "
                f"Individual latencies: {[f'{lat:.3f}s' for lat in latencies]}"
            )
            assert max_latency < 2.0, (
                f"Max RAG query latency {max_latency:.3f}s exceeds 2.0s threshold. "
                f"Individual latencies: {[f'{lat:.3f}s' for lat in latencies]}"
            )
        finally:
            source_file.unlink(missing_ok=True)

    @requires_both
    @pytest.mark.integration
    async def test_rag_write_back_knowledge_loop(self) -> None:
        pipeline = await _build_pipeline(reranker_enabled=False)

        analysis_text = (
            "Analysis Result: The login API endpoint /api/v2/login has a flaky defect. "
            "The failure rate is 3.5% across 1000 runs. "
            "Root cause: network timeout under high concurrency. "
            "Recommendation: implement connection pooling and increase timeout to 30s."
        )

        await pipeline.write_back(
            content=analysis_text,
            collection=TEST_COLLECTION,
            metadata={
                "source": "analyzer",
                "defect_type": "flaky",
                "severity": "major",
            },
        )

        results = await pipeline.query(
            query_text="login API flaky defect analysis",
            collection=TEST_COLLECTION,
            top_k=3,
        )
        assert len(results) > 0, "Query should return write-back analysis results"

        matched = False
        for r in results:
            if "flaky defect" in r.content or "login API" in r.content or "timeout" in r.content:
                matched = True
                assert float(r.score) >= 0.0
                break

        assert matched, (
            f"Write-back content should be retrievable via RAG query. "
            f"Results contents: {[r.content[:100] for r in results]}"
        )

        second_analysis = (
            "Follow-up: After implementing connection pooling on the login endpoint, "
            "the flaky rate dropped from 3.5% to 0.2%. The fix is confirmed effective."
        )
        await pipeline.write_back(
            content=second_analysis,
            collection=TEST_COLLECTION,
            metadata={
                "source": "analyzer",
                "defect_type": "flaky",
                "severity": "major",
                "follow_up": "true",
            },
        )

        follow_up_results = await pipeline.query(
            query_text="After implementing connection pooling on the login endpoint the flaky rate dropped",
            collection=TEST_COLLECTION,
            top_k=3,
        )
        assert len(follow_up_results) > 0, "Follow-up write-back content should also be retrievable"

        has_follow_up = any("connection pooling" in r.content or "0.2%" in r.content for r in follow_up_results)
        assert has_follow_up, (
            f"Follow-up analysis should be found in results. Contents: {[r.content[:100] for r in follow_up_results]}"
        )


class TestRAGPipelineHealthCheck:
    @requires_both
    @pytest.mark.integration
    async def test_health_check_all_ok(self) -> None:
        pipeline = await _build_pipeline()
        status = await pipeline.health_check()
        assert status["vector_store"] is True
        assert status["fulltext"] is True
        assert status["embedding"] is True
        assert status["degraded"] is False

    @pytest.mark.integration
    async def test_health_check_degraded(self) -> None:
        mock_vector_store = MagicMock(spec=IVectorStore)
        mock_vector_store.search = AsyncMock(
            side_effect=RAGSearchError(
                "Milvus unavailable",
                code="MILVUS_CONNECTION_FAILED",
            )
        )
        mock_vector_store.upsert = AsyncMock()
        mock_vector_store.delete = AsyncMock()

        mock_fulltext = MagicMock(spec=IFullTextSearch)
        mock_fulltext.search = AsyncMock(return_value=[{"id": "1", "score": 1.0, "document": "test", "metadata": {}}])
        mock_fulltext.index = AsyncMock()
        mock_fulltext.delete = AsyncMock()

        pipeline = await _build_pipeline(
            vector_store=mock_vector_store,
            fulltext=mock_fulltext,
        )
        status = await pipeline.health_check()
        assert status["vector_store"] is False
        assert status["fulltext"] is True
        assert status["degraded"] is True


class TestRAGPipelineEdgeCases:
    @requires_both
    @pytest.mark.integration
    async def test_query_empty_collection(self) -> None:
        pipeline = await _build_pipeline()

        results = await pipeline.query(
            query_text="nonexistent content",
            collection=TEST_COLLECTION,
            top_k=3,
        )
        assert len(results) == 0, "Query on empty collection should return no results"

    @requires_both
    @pytest.mark.integration
    async def test_ingest_empty_source(self) -> None:
        pipeline = await _build_pipeline()

        source_file = TEST_SAMPLE_DIR / "empty_test.md"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("", encoding="utf-8")

        try:
            chunk_count = await pipeline.ingest(
                source=str(source_file),
                collection=TEST_COLLECTION,
                metadata={"source_type": "empty_test"},
            )
            assert chunk_count == 0, "Ingest of empty file should return 0"
        finally:
            source_file.unlink(missing_ok=True)

    @requires_both
    @pytest.mark.integration
    async def test_is_embedding_degraded_property(self) -> None:
        pipeline = await _build_pipeline()
        assert pipeline.is_embedding_degraded is False

        mock_embedding = MagicMock(spec=IEmbeddingService)
        mock_embedding.embed = AsyncMock(
            side_effect=RAGDegradedError(
                "Simulated embedding failure",
                code="EMBED_SERVICE_DEGRADED",
            )
        )
        mock_embedding.embed_batch = AsyncMock(
            side_effect=RAGDegradedError(
                "Simulated embedding failure",
                code="EMBED_SERVICE_DEGRADED",
            )
        )

        failover = EmbeddingFailover(
            primary=mock_embedding,
            fallback=None,
            circuit_breaker_threshold=1,
        )
        degraded_pipeline = await _build_pipeline(embedding_service=failover)

        with pytest.raises(RAGDegradedError):
            await failover.embed("trigger failure")
        assert degraded_pipeline.is_embedding_degraded is True
