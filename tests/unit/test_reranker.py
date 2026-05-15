from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.rag.pipeline import RAGPipeline, RAGResult
from testagent.rag.reranker import (
    CrossEncoderReranker,
    IReranker,
    NoopReranker,
    RerankerFactory,
)


def _make_docs(n: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"doc{i}",
            "document": f"content of document {i}",
            "score": 1.0 - i * 0.1,
            "metadata": {"collection": "test"},
        }
        for i in range(n)
    ]


# ─────────────────────────────────────────────
# IReranker Protocol
# ─────────────────────────────────────────────


class TestIRerankerProtocol:
    def test_noop_reranker_conforms(self) -> None:
        assert isinstance(NoopReranker(), IReranker)

    def test_cross_encoder_reranker_conforms(self) -> None:
        reranker = CrossEncoderReranker.__new__(CrossEncoderReranker)
        reranker._model_name = "test"
        reranker._model = None
        assert isinstance(reranker, IReranker)


# ─────────────────────────────────────────────
# NoopReranker
# ─────────────────────────────────────────────


class TestNoopReranker:
    async def test_returns_first_top_k(self) -> None:
        reranker = NoopReranker()
        docs = _make_docs(10)

        result = await reranker.rerank(query="test", documents=docs, top_k=3)

        assert len(result) == 3
        assert result[0]["id"] == "doc0"
        assert result[1]["id"] == "doc1"
        assert result[2]["id"] == "doc2"

    async def test_returns_all_when_top_k_exceeds_count(self) -> None:
        reranker = NoopReranker()
        docs = _make_docs(3)

        result = await reranker.rerank(query="test", documents=docs, top_k=10)

        assert len(result) == 3

    async def test_empty_documents(self) -> None:
        reranker = NoopReranker()

        result = await reranker.rerank(query="test", documents=[], top_k=5)

        assert result == []

    async def test_default_top_k(self) -> None:
        reranker = NoopReranker()
        docs = _make_docs(10)

        result = await reranker.rerank(query="test", documents=docs)

        assert len(result) == 5

    async def test_does_not_modify_documents(self) -> None:
        reranker = NoopReranker()
        docs = _make_docs(3)
        original_ids = [d["id"] for d in docs]

        await reranker.rerank(query="test", documents=docs, top_k=2)

        assert [d["id"] for d in docs] == original_ids


# ─────────────────────────────────────────────
# CrossEncoderReranker
# ─────────────────────────────────────────────


class TestCrossEncoderReranker:
    @pytest.fixture
    def mock_model(self) -> MagicMock:
        model = MagicMock()
        model.predict = MagicMock(side_effect=lambda pairs: [float(10 - i) for i in range(len(pairs))])
        return model

    @pytest.fixture
    def reranker(self, mock_model: MagicMock) -> CrossEncoderReranker:
        r = CrossEncoderReranker.__new__(CrossEncoderReranker)
        r._model_name = "BAAI/bge-reranker-large"
        r._model = mock_model
        return r

    async def test_rerank_sorts_by_score_descending(
        self,
        reranker: CrossEncoderReranker,
    ) -> None:
        docs = _make_docs(5)

        result = await reranker.rerank(query="test", documents=docs, top_k=5)

        scores = [r["rerank_score"] for r in result]
        assert scores == sorted(scores, reverse=True)
        assert result[0]["rerank_score"] > result[-1]["rerank_score"]

    async def test_rerank_adds_rerank_score(
        self,
        reranker: CrossEncoderReranker,
    ) -> None:
        docs = _make_docs(3)

        result = await reranker.rerank(query="test", documents=docs, top_k=3)

        for doc in result:
            assert "rerank_score" in doc
            assert isinstance(doc["rerank_score"], float)

    async def test_rerank_truncates_to_top_k(
        self,
        reranker: CrossEncoderReranker,
    ) -> None:
        docs = _make_docs(10)

        result = await reranker.rerank(query="test", documents=docs, top_k=3)

        assert len(result) == 3

    async def test_rerank_empty_documents(
        self,
        reranker: CrossEncoderReranker,
    ) -> None:
        result = await reranker.rerank(query="test", documents=[], top_k=5)

        assert result == []

    async def test_rerank_preserves_document_fields(
        self,
        reranker: CrossEncoderReranker,
    ) -> None:
        docs = _make_docs(2)

        result = await reranker.rerank(query="test", documents=docs, top_k=2)

        for doc in result:
            assert "id" in doc
            assert "document" in doc
            assert "metadata" in doc
            assert "rerank_score" in doc

    async def test_rerank_reorders_by_model_score(self) -> None:
        mock_model = MagicMock()
        scores = [0.1, 0.9, 0.5]
        mock_model.predict = MagicMock(return_value=scores)

        reranker = CrossEncoderReranker.__new__(CrossEncoderReranker)
        reranker._model_name = "test"
        reranker._model = mock_model

        docs = [
            {"id": "doc0", "document": "a", "score": 0.5, "metadata": {}},
            {"id": "doc1", "document": "b", "score": 0.3, "metadata": {}},
            {"id": "doc2", "document": "c", "score": 0.8, "metadata": {}},
        ]

        result = await reranker.rerank(query="test", documents=docs, top_k=3)

        assert result[0]["id"] == "doc1"
        assert result[0]["rerank_score"] == pytest.approx(0.9)
        assert result[1]["id"] == "doc2"
        assert result[1]["rerank_score"] == pytest.approx(0.5)
        assert result[2]["id"] == "doc0"
        assert result[2]["rerank_score"] == pytest.approx(0.1)

    async def test_rerank_uses_content_field_fallback(self) -> None:
        mock_model = MagicMock()
        mock_model.predict = MagicMock(return_value=[0.5])

        reranker = CrossEncoderReranker.__new__(CrossEncoderReranker)
        reranker._model_name = "test"
        reranker._model = mock_model

        docs = [{"id": "doc0", "content": "content field", "score": 0.5, "metadata": {}}]

        result = await reranker.rerank(query="test", documents=docs, top_k=1)

        assert len(result) == 1
        assert result[0]["id"] == "doc0"

    @patch("testagent.rag.reranker.CrossEncoderReranker._ensure_model")
    async def test_rerank_calls_ensure_model(
        self,
        mock_ensure: MagicMock,
    ) -> None:
        mock_model = MagicMock()
        mock_model.predict = MagicMock(return_value=[0.5])
        mock_ensure.return_value = mock_model

        reranker = CrossEncoderReranker(model_name="test-model")
        docs = _make_docs(1)

        await reranker.rerank(query="test", documents=docs, top_k=1)

        mock_ensure.assert_called_once()


# ─────────────────────────────────────────────
# RerankerFactory
# ─────────────────────────────────────────────


class TestRerankerFactory:
    def test_create_noop_when_disabled(self) -> None:
        reranker = RerankerFactory.create(reranker_enabled=False)

        assert isinstance(reranker, NoopReranker)

    def test_create_cross_encoder_when_enabled(self) -> None:
        reranker = RerankerFactory.create(reranker_enabled=True, reranker_model="custom-model")

        assert isinstance(reranker, CrossEncoderReranker)
        assert reranker._model_name == "custom-model"

    def test_create_cross_encoder_default_model(self) -> None:
        reranker = RerankerFactory.create(reranker_enabled=True)

        assert isinstance(reranker, CrossEncoderReranker)
        assert reranker._model_name == "BAAI/bge-reranker-large"

    def test_create_default_is_noop(self) -> None:
        reranker = RerankerFactory.create()

        assert isinstance(reranker, NoopReranker)


# ─────────────────────────────────────────────
# RAGPipeline integration with reranker
# ─────────────────────────────────────────────


class TestRAGPipelineRerankerIntegration:
    @pytest.fixture
    def mock_embedding(self) -> MagicMock:
        svc = MagicMock()

        async def embed_side(text: str) -> list[float]:
            return [0.1, 0.2, 0.3]

        svc.embed = AsyncMock(side_effect=embed_side)
        return svc

    @pytest.fixture
    def mock_vector_store(self) -> MagicMock:
        store = MagicMock()

        async def search_side(
            query_vector: list[float],
            top_k: int = 10,
            filters: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]:
            return [
                {
                    "id": "vec_doc1",
                    "score": 0.9,
                    "metadata": {"collection": "test_coll"},
                    "document": "vector result 1",
                },
                {
                    "id": "vec_doc2",
                    "score": 0.8,
                    "metadata": {"collection": "test_coll"},
                    "document": "vector result 2",
                },
            ]

        store.search = AsyncMock(side_effect=search_side)
        return store

    @pytest.fixture
    def mock_fulltext(self) -> MagicMock:
        ft = MagicMock()

        async def search_side(
            query: str,
            top_k: int = 10,
            filters: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]:
            return [
                {
                    "id": "kw_doc1",
                    "score": 0.85,
                    "metadata": {"collection": "test_coll"},
                    "document": "keyword result 1",
                },
                {
                    "id": "kw_doc2",
                    "score": 0.75,
                    "metadata": {"collection": "test_coll"},
                    "document": "keyword result 2",
                },
            ]

        ft.search = AsyncMock(side_effect=search_side)
        return ft

    @pytest.fixture
    def mock_reranker(self) -> MagicMock:
        reranker = MagicMock()

        async def rerank_side(
            query: str,
            documents: list[dict[str, Any]],
            top_k: int = 5,
        ) -> list[dict[str, Any]]:
            sorted_docs = sorted(documents, key=lambda d: d.get("score", 0), reverse=True)
            result = []
            for doc in sorted_docs[:top_k]:
                doc_copy = dict(doc)
                doc_copy["rerank_score"] = doc.get("score", 0) + 0.05
                result.append(doc_copy)
            return result

        reranker.rerank = AsyncMock(side_effect=rerank_side)
        return reranker

    async def test_query_calls_reranker(
        self,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
        mock_reranker: MagicMock,
    ) -> None:
        pipeline = RAGPipeline(
            embedding_service=mock_embedding,
            vector_store=mock_vector_store,
            fulltext=mock_fulltext,
            reranker=mock_reranker,
        )

        await pipeline.query(
            query_text="test query",
            collection="test_coll",
            top_k=2,
        )

        mock_reranker.rerank.assert_awaited_once()
        call_kwargs = mock_reranker.rerank.call_args
        assert call_kwargs.kwargs["query"] == "test query"
        assert call_kwargs.kwargs["top_k"] == 2

    async def test_query_uses_rerank_score(
        self,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
        mock_reranker: MagicMock,
    ) -> None:
        pipeline = RAGPipeline(
            embedding_service=mock_embedding,
            vector_store=mock_vector_store,
            fulltext=mock_fulltext,
            reranker=mock_reranker,
        )

        results = await pipeline.query(
            query_text="test query",
            collection="test_coll",
            top_k=5,
        )

        for r in results:
            assert isinstance(r, RAGResult)
            assert isinstance(r.score, float)

    async def test_query_without_reranker_uses_noop(
        self,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
    ) -> None:
        pipeline = RAGPipeline(
            embedding_service=mock_embedding,
            vector_store=mock_vector_store,
            fulltext=mock_fulltext,
        )

        results = await pipeline.query(
            query_text="test query",
            collection="test_coll",
            top_k=2,
        )

        assert isinstance(pipeline._reranker, NoopReranker)
        assert len(results) <= 2
        for r in results:
            assert isinstance(r, RAGResult)

    async def test_query_passes_fused_results_to_reranker(
        self,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
        mock_reranker: MagicMock,
    ) -> None:
        pipeline = RAGPipeline(
            embedding_service=mock_embedding,
            vector_store=mock_vector_store,
            fulltext=mock_fulltext,
            reranker=mock_reranker,
        )

        await pipeline.query(
            query_text="test query",
            collection="test_coll",
            top_k=5,
        )

        rerank_docs = mock_reranker.rerank.call_args.kwargs["documents"]
        assert len(rerank_docs) > 0
        for doc in rerank_docs:
            assert "id" in doc
            assert "document" in doc
            assert "score" in doc

    async def test_query_top_k_limits_output(
        self,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
        mock_reranker: MagicMock,
    ) -> None:
        pipeline = RAGPipeline(
            embedding_service=mock_embedding,
            vector_store=mock_vector_store,
            fulltext=mock_fulltext,
            reranker=mock_reranker,
        )

        results = await pipeline.query(
            query_text="test query",
            collection="test_coll",
            top_k=1,
        )

        assert len(results) <= 1

    async def test_query_with_empty_search_results(
        self,
        mock_embedding: MagicMock,
        mock_reranker: MagicMock,
    ) -> None:
        empty_store = MagicMock()
        empty_store.search = AsyncMock(return_value=[])
        empty_ft = MagicMock()
        empty_ft.search = AsyncMock(return_value=[])

        pipeline = RAGPipeline(
            embedding_service=mock_embedding,
            vector_store=empty_store,
            fulltext=empty_ft,
            reranker=mock_reranker,
        )

        results = await pipeline.query(
            query_text="test query",
            collection="empty_coll",
            top_k=5,
        )

        assert results == []
        mock_reranker.rerank.assert_not_awaited()
